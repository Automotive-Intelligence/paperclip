"""services/slipstream_engine.py -- the Railway Slipstream engine orchestrator.

run_brand() produces ONE full-Slipstream post for a brand and publishes it, or
HOLDS on any gate violation. Every stage is an importable module function so it
is testable in isolation and observable in railway logs. Fire it on demand via
POST /admin/run-slipstream/{brand}; schedule it MWF once proven.

"Publish" depends on where the brand lives. Repo brands (mdx, ts_posts_array) get
a PR that is merged on a green Vercel build. Storefront brands (shopify_article,
i.e. Paper & Purpose) have no repo at all: they get an UNPUBLISHED Shopify article
draft via services/slipstream_shopify, and a human sets it Visible. That path
never opens a PR, never auto-merges and never fires social.
"""
from __future__ import annotations

import base64
import logging
import os
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, Optional

import requests
import yaml

from datetime import timedelta

from services.slipstream_assemble import assemble_mdx, assemble_ts_posts, assemble_tsx_post
from services.slipstream_factcheck import (pr_table, resolve_factcheck_cfg,
                                           run_gate as run_factcheck, summarize as _fc_summary)
from services.slipstream_generate import EmptyAtLimit, generate_post, revise_post
from services.slipstream_github import merge_when_green, publish_post
from services.slipstream_images import generate_images
from services.slipstream_repair import (pr_section as _repair_pr_section, repair_post,
                                        summarize as _repair_summary)
from services.slipstream_research import (build_source_pack, pack_failures,
                                          pr_section as _research_pr_section,
                                          research_enabled, resolve_research_cfg,
                                          summarize as _research_summary)
from services.slipstream_shopify import assemble_shopify_article, create_article_draft

logger = logging.getLogger(__name__)


class QueueExhausted(Exception):
    """A brand's topic queue has no unchecked '- [ ]' item left.

    A DISTINCT type (not a bare ValueError) so run_brand can surface an exhausted
    queue as a VISIBLE signal -- a loud log + a distinct receipt reason -- instead
    of a silent generic HOLD. An exhausted queue makes the brand produce NOTHING
    every run until it is refilled, and until this type existed that miss reached
    no rail (the 2026-07-31 BAE-class silent hold)."""


def _social_distribution_enabled() -> bool:
    """Kill-switch for social auto-distribution. Social auto-fires on every
    auto-published post and only became live when auto-merge was fixed (PR #226),
    so it is deliberately behind a documented flag: default ON (the desired
    behavior), but SLIPSTREAM_SOCIAL_DISTRIBUTE=0/false/no/off pauses it WITHOUT a
    redeploy if a Zernio issue ever needs it stopped fast."""
    return os.getenv("SLIPSTREAM_SOCIAL_DISTRIBUTE", "1").strip().lower() not in (
        "0", "false", "no", "off")


def _site_root(live_url: str) -> str:
    """https://host/blog/slug -> https://host"""
    return live_url.rsplit("/blog/", 1)[0]


# A merged blog PR starts a NEW production deploy; the article and its hero image
# 404 on the live domain until that deploy finishes (one to two minutes). The
# loader re-hosts media onto Zernio's CDN at schedule time and SKIPS any post
# whose image does not resolve, permanently. Calling it the instant the merge
# returned skipped every AIPG and AvI blog post from 2026-09-07 to 2026-10-21
# (127 posts, reason media_unreachable, all logged and nobody saw). So wait for
# the live site first.
LIVE_WAIT_TIMEOUT_S = 600
LIVE_WAIT_POLL_S = 15


def _url_live(url: str) -> bool:
    try:
        r = requests.get(url, timeout=20, allow_redirects=True, stream=True)
        r.close()
        return r.status_code == 200
    except requests.RequestException:
        return False


def _wait_until_live(urls, timeout_s: float = LIVE_WAIT_TIMEOUT_S,
                     poll_s: float = LIVE_WAIT_POLL_S, check=None, sleep=None,
                     clock=None) -> list:
    """Poll until every url answers 200. Returns the urls still NOT live (empty
    list = all live). Never raises."""
    import time as _t
    check = check or _url_live
    sleep = sleep or _t.sleep
    clock = clock or _t.monotonic
    pending = [u for u in urls if u]
    deadline = clock() + timeout_s
    while pending:
        pending = [u for u in pending if not check(u)]
        if not pending or clock() >= deadline:
            break
        sleep(poll_s)
    return pending


def _distribute_social(cfg: dict, post: dict, slug: str, live_url: str) -> dict:
    """Best-effort: schedule the social pack via the ONE loader (Zernio for
    own-brands; the same tools/social_load loader every other rail uses -- no new
    posting path). NEVER fails the post (Zernio limits / not-connected are
    non-fatal), but the outcome is LOGGED loudly either way so a swallowed Zernio
    failure never hides (the #228 lesson)."""
    brand = cfg.get("business_key", cfg.get("brand_key", "?"))
    if not _social_distribution_enabled():
        logger.info("[slipstream] social distribution DISABLED via SLIPSTREAM_SOCIAL_DISTRIBUTE "
                    "for %s (%s); blog is live but no social was scheduled", brand, slug)
        return {"ok": False, "disabled": True, "note": "SLIPSTREAM_SOCIAL_DISTRIBUTE off"}
    try:
        from services.social_load_service import run_social_load
        social = post.get("social") or {}
        base = datetime.now(timezone.utc) + timedelta(days=1)
        accounts = cfg.get("zernio_accounts") or {}
        jobs = []
        # ALL connected platforms, not just linkedin/x. Book'd (facebook +
        # instagram) and Agent Empire (facebook + instagram + youtube) have
        # NEITHER linkedin nor x, so the old two-platform loop could never build
        # a single job for them: every blog they published reached no social
        # audience at all, silently, and only logged "no social drafts".
        # Fall back across platforms so an older post that only carries
        # linkedin/x copy still reaches facebook and instagram.
        def _copy_for(p_):
            order = {"facebook": ("facebook", "linkedin", "x"),
                     "instagram": ("instagram", "x", "linkedin"),
                     "linkedin": ("linkedin", "facebook", "x"),
                     "x": ("x", "instagram", "linkedin")}[p_]
            for k in order:
                v = str(social.get(k) or "").strip()
                if v:
                    return v
            return ""

        for i, platform in enumerate(("linkedin", "x", "facebook", "instagram")):
            text = _copy_for(platform)
            if not text:
                continue
            # An unresolved account id MUST NOT be passed through as None: the
            # Zernio client reads that as "every connected account on this
            # platform" and cross-posts this brand onto all the others. Skip the
            # platform loudly instead. AIPG and BAE legitimately have no LinkedIn
            # or X account, so skipping is the correct outcome for them, not a bug.
            account_id = accounts.get(platform)
            if not account_id:
                logger.warning(
                    "[slipstream] no zernio account for %s/%s; SKIPPING that platform "
                    "rather than posting to every connected account", brand, platform)
                continue
            when = (base + timedelta(days=i, hours=i * 4)).strftime("%Y-%m-%dT%H:%M:%S")
            jobs.append({
                "brand": cfg["business_key"], "platform": platform,
                "content": f"{text}\n\n{live_url}", "scheduled_for": when,
                "content_id": slug, "entry_point": "blog_engine",
                "account_id": account_id,
                # MUST match the img_prefix rule used when the files are
                # committed: tsx_post serves from /img/, everything else from
                # /blog/. Hardcoding /blog/ here produced a 404 media URL for
                # Book'd, and Zernio REJECTS an Instagram post whose media does
                # not resolve, so every IG post for that brand failed at the API.
                "media_urls": [f"{_site_root(live_url)}/"
                               f"{'img' if cfg.get('format') == 'tsx_post' else 'blog'}"
                               f"/{slug}-hero.png"],
            })
        if not jobs:
            logger.warning("[slipstream] no social drafts to distribute for %s (%s)", brand, slug)
            return {"ok": False, "note": "no social drafts"}
        # MANUAL brands (social_mode: export) never touch Zernio. The pack goes
        # to the outbox for Michael to post by hand; returning before the loader
        # is what actually saves the money.
        if str(cfg.get("social_mode") or "zernio").lower() == "export":
            from services.social_outbox import export
            tok = os.environ.get("SLIPSTREAM_GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
            if not tok:
                logger.error("[slipstream] %s is export-mode but no GH token; pack NOT written "
                             "for %s -- these posts would be silently lost", brand, slug)
                return {"ok": False, "error": "export mode: no github token"}
            items = [{"platform": j["platform"], "when": j["scheduled_for"].replace("T", " "),
                      "text": j["content"], "image_url": (j.get("media_urls") or [None])[0]}
                     for j in jobs]
            r = export(brand, cfg.get("display_name", brand),
                       f"{datetime.now(timezone.utc):%Y-%m-%d}-{slug}", items, tok)
            logger.info("[slipstream] %s EXPORTED %d post(s) to %s (manual posting, "
                        "no zernio)", brand, r["posts"], r["folder"])
            return {"ok": True, "exported": True, **r}
        must_be_live = [live_url] + sorted({u for j in jobs for u in (j.get("media_urls") or [])})
        not_live = _wait_until_live(must_be_live)
        if not_live:
            logger.error("[slipstream] %s (%s): live site still missing %s after %ss; social "
                         "NOT scheduled (the loader would skip every post as media_unreachable)",
                         brand, slug, not_live, LIVE_WAIT_TIMEOUT_S)
            return {"ok": False, "error": "not live after wait", "not_live": not_live}
        result = run_social_load(jobs, commit=True)
        if result.get("ok"):
            logger.info("[slipstream] social scheduled for %s (%s): counts=%s",
                        brand, slug, result.get("counts"))
        else:
            logger.warning("[slipstream] social distribution INCOMPLETE for %s (%s): %s",
                           brand, slug, result.get("error") or result.get("counts"))
        return result
    except Exception as e:
        logger.warning("[slipstream] social distribution failed (non-fatal) for %s (%s): %s",
                       brand, slug, e)
        return {"ok": False, "error": str(e)}

_CFG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "slipstream_brands.yaml")


@lru_cache(maxsize=1)
def _load_cfg() -> dict:
    with open(_CFG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _brand_cfg(brand_key: str) -> dict:
    full = _load_cfg()
    cfg = (full.get("brands") or {}).get(brand_key)
    if not cfg:
        raise ValueError(f"unknown brand '{brand_key}' (not in config/slipstream_brands.yaml)")
    merged = {**cfg, "brand_key": brand_key}
    # The Vercel team is a top-level default shared by every brand project; the
    # per-brand vercel_project_id scopes the build-verification lookup.
    merged.setdefault("vercel_team_id", full.get("vercel_team_id"))
    # Zernio account ids live at the top level keyed by brand_key, so a brand's
    # social routing is declared in one table rather than scattered per brand.
    merged.setdefault("zernio_accounts",
                      (full.get("zernio_accounts") or {}).get(brand_key) or {})
    # Fact-check gate config (banned content, facts file) lives in the top-level
    # `factcheck:` block: defaults for every brand plus per-brand additions.
    merged.setdefault("factcheck", resolve_factcheck_cfg(full, brand_key))
    # Research-before-writing config (search caps, domain lists) lives in the
    # top-level `research:` block. ON for gated brands, OFF for a brand opted
    # out of the gate, unless a brand says otherwise.
    merged.setdefault("research", resolve_research_cfg(full, brand_key))
    return merged


def due_today(cadence: str, today=None) -> bool:
    """Should a brand publish on this run?

    The MWF job published EVERY enabled brand on every Mon/Wed/Fri, so the only
    volume controls were "on" and "off". Michael asked to slow WD, Book'd and
    Agent Empire without silencing them, which needs a middle setting.

      mwf      3x/week, Mon+Wed+Fri (the default, unchanged)
      weekly   1x/week, Mondays only
      biweekly 1x/fortnight, Mondays of even ISO weeks
      off      never on the cron; run_brand still works by hand
    """
    from zoneinfo import ZoneInfo
    d = today or datetime.now(ZoneInfo("America/Chicago")).date()
    c = (cadence or "mwf").strip().lower()
    if c == "off":
        return False
    if c == "mwf":
        return d.weekday() in (0, 2, 4)
    if c == "weekly":
        return d.weekday() == 0
    if c == "biweekly":
        return d.weekday() == 0 and d.isocalendar()[1] % 2 == 0
    return d.weekday() in (0, 2, 4)      # unknown value behaves like the default


def _next_topic(cfg: dict, token: str) -> str:
    """Read the brand's queue via GitHub REST and return the first unchecked topic."""
    # NOT cfg.get("queue_repo", cfg["repo"]): a .get default is evaluated
    # EAGERLY, so that form raises KeyError on a storefront brand (P&P)
    # which has no repo at all. Storefront brands still keep their queue
    # on GitHub, so queue_repo is the only source that must resolve.
    qrepo = cfg.get("queue_repo") or cfg.get("repo")
    url = f"https://api.github.com/repos/{qrepo}/contents/{cfg['queue_path']}"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}",
                                   "Accept": "application/vnd.github+json"}, timeout=30)
    if not r.ok:
        raise ValueError(f"cannot read queue {cfg['queue_path']}: {r.status_code}")
    text = base64.b64decode(r.json()["content"]).decode("utf-8")
    m = re.search(r"^- \[ \]\s*(.+)$", text, re.M)
    if not m:
        # DISTINCT signal (not a generic ValueError): an exhausted queue is a
        # refill problem, not a code fault, and must reach the rail as such.
        raise QueueExhausted(
            f"queue exhausted: no unchecked '- [ ]' topic in {qrepo}/{cfg['queue_path']}")
    return m.group(1).strip()


def _checkoff_topic(cfg: dict, topic: str, live_url: str, token: str) -> bool:
    """Mark '- [ ] {topic}' as '- [x] {topic} -> {live_url}' in the queue so a
    scheduled run never republishes the same topic. Best-effort (returns False
    if the topic is not a queue line, e.g. an on-demand explicit topic)."""
    try:
        # NOT cfg.get("queue_repo", cfg["repo"]): a .get default is evaluated
        # EAGERLY, so that form raises KeyError on a storefront brand (P&P)
        # which has no repo at all. Storefront brands still keep their queue
        # on GitHub, so queue_repo is the only source that must resolve.
        qrepo = cfg.get("queue_repo") or cfg.get("repo")
        url = f"https://api.github.com/repos/{qrepo}/contents/{cfg['queue_path']}"
        h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        r = requests.get(url, headers=h, timeout=30)
        if not r.ok:
            return False
        data = r.json()
        text = base64.b64decode(data["content"]).decode("utf-8")
        pattern = re.compile(r"^- \[ \]\s*" + re.escape(topic) + r".*$", re.M)
        if not pattern.search(text):
            return False
        new_text = pattern.sub(f"- [x] {topic} → {live_url}", text, count=1)
        requests.put(url, headers=h, timeout=30, json={
            "message": f"content-queue: mark shipped ({topic[:50]})",
            "content": base64.b64encode(new_text.encode("utf-8")).decode("ascii"),
            "sha": data["sha"],
        })
        return True
    except Exception as e:
        logger.warning("[slipstream] queue check-off failed (non-fatal): %s", e)
        return False


def _social_md(post: Dict[str, Any]) -> str:
    s = post.get("social") or {}
    return (f"# Social pack: {post['title']}\n\n## LinkedIn\n\n{s.get('linkedin','')}\n\n"
            f"## X\n\n{s.get('x','')}\n")


def _pr_body(post: Dict[str, Any], image_count: int, fmt: str = "mdx",
             factcheck: Optional[Dict[str, Any]] = None,
             research: Optional[Dict[str, Any]] = None) -> str:
    gate = "validate_blocks" if fmt == "ts_posts_array" else "validate_post"
    if factcheck and factcheck.get("ran"):
        gate += " + factcheck"
    return (f"Automated Slipstream post (Railway engine).\n\n- slug: `{post['slug']}`\n"
            f"- format: `{fmt}`\n"
            f"- images: {image_count} (hero + in-body)\n- gates: passed ({gate})\n\n"
            "Vercel preview builds this PR = the build-gate. Review + merge to publish."
            + _research_pr_section(research, post)
            + pr_table(factcheck)
            + _repair_pr_section((factcheck or {}).get("repair")))


def needs_editor_section(post: Dict[str, Any], violations: list, factcheck: Optional[Dict[str, Any]],
                         pack: Optional[Dict[str, Any]], cfg: Dict[str, Any]) -> str:
    """The NEEDS EDITOR worklist for a held post kept as a draft: each failing
    sentence, the rule it broke, and the pack quote(s) it cites, so a person can
    finish the post by hand. Plain markdown; used in the draft PR body and by
    the dry-run script."""
    from services.slipstream_repair import _quotes_for
    from services import slipstream_factcheck as _fc
    lines = ["\n\n## NEEDS EDITOR\n",
             "**This post did NOT pass the fact-check gate. Do not merge it as it stands.** "
             "It is kept as a draft so a person can finish it. Fix or cut each sentence below, "
             "then re-run the gate (`scripts/slipstream_gate_check.py`) before publishing. "
             "Images have not been generated for it.\n"]
    failures = list((factcheck or {}).get("failures") or [])
    try:
        units, post_level = _fc.extract_post(post)
    except Exception:
        units, post_level = [], []
    by_text = {u.text: u for u in units}
    seen: Dict[str, Dict[str, Any]] = {}
    for f in failures:
        if not f.get("text"):
            continue
        row = seen.setdefault(f["text"], {"where": f.get("where", ""), "rules": [], "violations": []})
        if f["rule"] not in row["rules"]:
            row["rules"].append(f["rule"])
        if f["violation"] not in row["violations"]:
            row["violations"].append(f["violation"])
    if seen:
        lines.append(f"### Failing sentences ({len(seen)})\n")
        for i, (text, row) in enumerate(seen.items(), 1):
            lines.append(f"{i}. **[{row['where']}]** {text}")
            lines.append(f"   - rule: {', '.join(row['rules'])}")
            unit = by_text.get(text)
            quotes = _quotes_for(unit, pack, cfg.get("domain", ""), post_level) if unit is not None else []
            if any(not r.startswith("editor:") for r in row["rules"]):
                for q in quotes[:3]:
                    lines.append(f'   - cited quote: "{q}"')
                if not quotes:
                    lines.append("   - cited quote: none (no pack source is linked to this sentence)")
            lines.append("")
    covered = {v for row in seen.values() for v in row["violations"]}
    rest = [v for v in violations if v not in covered]
    if rest:
        lines.append(f"### Other reasons it was held ({len(rest)})\n")
        lines += [f"- {v}" for v in rest]
    if not seen and not rest:
        lines.append("- (the gate gave no sentence-level detail)")
    return "\n".join(lines) + "\n"


def _held_draft_pr(cfg: Dict[str, Any], post: Dict[str, Any], violations: list,
                   factcheck: Optional[Dict[str, Any]], research: Optional[Dict[str, Any]],
                   pack: Optional[Dict[str, Any]], assemble, fmt: str, date_str: str,
                   token: str) -> Dict[str, Any]:
    """Open a DRAFT pull request for a held post. Never raises, never merges,
    never buys images, never checks off the topic, never schedules social.
    Returns the keys to add to the HOLD receipt."""
    brand_key = cfg.get("brand_key", "?")
    if fmt == "shopify_article" or not cfg.get("repo"):
        return {"draft_pr": None, "draft_pr_note": "on_hold: draft_pr needs a repo brand; post held"}
    try:
        payload, tsx_files, fmt_violations = assemble(post)
        if fmt_violations or (not payload and not tsx_files):
            return {"draft_pr": None, "draft_pr_note": "held post does not pass the format gate, so no "
                                                       "draft was written: " + "; ".join(fmt_violations or [])}
        slug = post["slug"]
        if fmt == "ts_posts_array":
            files: Dict[str, Any] = {cfg["posts_file"]: payload}
        elif fmt == "tsx_post":
            files = dict(tsx_files or {})
        else:
            files = {f"{cfg['blog_dir']}/{slug}.mdx": payload,
                     f"{cfg['blog_dir']}/{slug}.social.md": _social_md(post)}
        body = ("**NEEDS EDITOR. HELD by the fact-check gate. Draft only: do not merge until every "
                "item under NEEDS EDITOR is fixed and the gate passes.**\n\n"
                f"- slug: `{slug}`\n- format: `{fmt}`\n- images: none generated (held post)\n"
                + needs_editor_section(post, violations, factcheck, pack, cfg)
                + _research_pr_section(research, post) + pr_table(factcheck)
                + _repair_pr_section((factcheck or {}).get("repair")))
        url = publish_post(cfg["repo"], f"slipstream-draft/{slug}-{date_str}", files,
                           f"[NEEDS EDITOR] content: {post['title']}", body, token, draft=True)
        logger.warning("[slipstream] %s HELD post kept as a DRAFT PR for a human editor: %s", brand_key, url)
        return {"draft_pr": url, "published": False,
                "draft_pr_note": "held post kept as a GitHub draft PR; never auto-merged, no social"}
    except Exception as e:
        logger.exception("[slipstream] %s could not open the draft PR for a held post", brand_key)
        return {"draft_pr": None, "draft_pr_note": f"draft PR failed ({type(e).__name__}: {e}); post held"}


def _research_stage(cfg: Dict[str, Any], topic: str, link_cache: Dict[str, Any]):
    """Research before writing. Returns (pack, hold).

    pack is None when research is off for this brand (the writer then gets
    exactly the prompt it always got). `hold` is a ready HOLD receipt when
    research itself failed on a brand that requires it: FAIL-CLOSED, the post is
    never written from memory instead."""
    if not research_enabled(cfg):
        return None, None
    brand_key = cfg.get("brand_key", "?")
    try:
        pack = build_source_pack(cfg, topic, link_cache=link_cache)
        if not isinstance(pack, dict) or not isinstance(pack.get("sources"), list):
            raise ValueError("research returned no source pack")
        if pack.get("mode") != "sourced":
            # THIN after the one broadened retry. The 2026-11-15 dry runs showed a
            # post written from a thin pack cannot pass the gate, so the writer
            # and both gate passes would be money spent on a certain HOLD.
            msg = (f"not enough verified sources for this topic "
                   f"({pack.get('usable_sources', len(pack['sources']))} usable, "
                   f"{pack.get('min_sources')} needed, after one broadened retry); "
                   "post HELD before the writer ran")
            logger.warning("[slipstream] %s HELD: %s", brand_key, msg)
            return None, {"ok": False, "held": True, "reason": "thin_pack", "violations": [msg],
                          "error": "not enough verified sources", "research": _research_summary(pack)}
        return pack, None
    except Exception as e:
        msg = (f"research failed (post HELD, never written from memory): "
               f"{type(e).__name__}: {e}")[:400]
        logger.exception("[slipstream] %s research failed; post HELD", brand_key)
        return None, {"ok": False, "held": True, "reason": "research_error",
                      "violations": [msg], "error": "research failed"}


def _with_pack_check(r: Dict[str, Any], post: Dict[str, Any], cfg: Dict[str, Any],
                     pack: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Add to one gate result (when the brand researched) the code check that
    every link in the post came from the verified pack. It can only ADD
    violations; it never clears one the gate raised."""
    if pack is not None and not r.get("error"):
        found = pack_failures(post, pack, cfg.get("domain", ""))
        extra = list(dict.fromkeys(f["violation"] for f in found))
        if extra:
            r = dict(r)
            r["failures"] = list(r.get("failures") or []) + found
            r["violations"] = extra + [v for v in (r.get("violations") or []) if v not in extra]
            r["ok"] = False
            r["counts"] = {**(r.get("counts") or {}), "links_outside_pack": len(extra),
                           "violations": len(r["violations"])}
    return r


def _factcheck_stage(cfg: Dict[str, Any], topic: str, post: Dict[str, Any], reassemble=None,
                     pack: Optional[Dict[str, Any]] = None,
                     link_cache: Optional[Dict[str, Any]] = None):
    """The fact-check + editor gate, with ONE writer revision.

    Returns (post, reassembled, summary, violations). `violations` empty means
    the post may go on; otherwise the caller HOLDS it. `reassembled` is what
    `reassemble(revised_post)` returned when a revision passed, so the caller
    publishes the REVISED payload, never the one the gate rejected.

    Bounded cost: at most one revision and two gate passes, and the second pass
    skips the paid model calls when the free code checks already fail.
    Kill-safe: a gate error, a failed revision, or anything unexpected HOLDS.
    """
    if (cfg.get("factcheck") or {}).get("enabled") is False:
        # Brand explicitly opted out in config (factcheck.brands.<key>.enabled: false).
        # Only a literal False skips; a missing or malformed value still gates.
        logger.info("[slipstream] %s factcheck gate OFF for this brand by config",
                    cfg.get("brand_key", "?"))
        return post, None, None, []
    # The research stage already fetched every pack source and every verified
    # internal link into this cache, so the gate does not fetch them again.
    cache: Dict[str, Any] = link_cache if link_cache is not None else {}
    # Sentences the verifier SUPPORTED with a code-confirmed quote are carried to
    # the later passes while they stay byte-identical (same text, same links).
    verdicts: Dict[Any, Any] = {}
    # The editor's verdict on a line is carried the same way, so an unchanged
    # line cannot pass on one look and fail on the next.
    edits: Dict[Any, Any] = {}
    try:
        r1 = _with_pack_check(run_factcheck(post, cfg, link_cache=cache, verdict_cache=verdicts,
                                            editor_cache=edits),
                              post, cfg, pack)
    except Exception as e:  # run_gate never raises; this guards a broken import/stub
        msg = f"factcheck gate error (post HELD, never published unchecked): {type(e).__name__}: {e}"
        return post, None, {"ran": True, "passed": False, "revised": False, "error": msg,
                            "model": None, "counts": {}, "claims": []}, [msg]
    if r1.get("ok"):
        return post, None, _fc_summary([r1], False), []
    if r1.get("error"):
        # The gate itself failed. A revision cannot fix that, so do not pay for one.
        return post, None, _fc_summary([r1], False), list(r1.get("violations") or ["factcheck gate error"])
    try:
        if pack is not None:
            # Same pack as the first draft: a revision cannot bring in a new source.
            revised = revise_post(cfg, topic, post, list(r1["violations"]), pack=pack)
        else:
            revised = revise_post(cfg, topic, post, list(r1["violations"]))
        revised["slug"] = post["slug"]
        revised["image_prompts"] = post["image_prompts"]
        reassembled = reassemble(revised) if reassemble else None
    except EmptyAtLimit as e:
        # The writer spent its whole output budget and returned no post. That is
        # a HOLD with its own reason, never a silent retry loop.
        logger.error("[slipstream] %s revision came back empty at the token limit", cfg.get("brand_key", "?"))
        return post, None, _fc_summary([r1], False), \
            list(r1["violations"]) + [f"revision came back empty at the token limit ({e}); "
                                      "post HELD, original violations stand"]
    except Exception as e:
        logger.exception("[slipstream] %s revision after factcheck failed", cfg.get("brand_key", "?"))
        return post, None, _fc_summary([r1], False), \
            list(r1["violations"]) + [f"revision failed ({type(e).__name__}: {e}); original violations stand"]
    fmt_violations = list(reassembled[-1] or []) if reassembled else []
    if fmt_violations:
        return revised, None, _fc_summary([r1], True), \
            ["revised post failed the format gate"] + fmt_violations
    try:
        r2 = _with_pack_check(
            run_factcheck(revised, cfg, link_cache=cache, skip_llm_on_deterministic_fail=True,
                          verdict_cache=verdicts, editor_cache=edits),
            revised, cfg, pack)
    except Exception as e:
        msg = f"factcheck gate error (post HELD, never published unchecked): {type(e).__name__}: {e}"
        return revised, None, _fc_summary([r1], True), [msg]
    summary = _fc_summary([r1, r2], True)
    if r2.get("ok"):
        return revised, reassembled, summary, []
    if r2.get("error") or (cfg.get("factcheck") or {}).get("repair_pass") is False:
        return revised, None, summary, list(r2.get("violations") or ["factcheck gate failed"])
    return _repair_stage(cfg, revised, [r1, r2], reassemble, pack, cache, verdicts, edits)


def _repair_stage(cfg: Dict[str, Any], revised: Dict[str, Any], passes: list, reassemble,
                  pack: Optional[Dict[str, Any]], cache: Dict[str, Any], verdicts: Dict[Any, Any],
                  edits: Optional[Dict[Any, Any]] = None):
    """The LAST step: one targeted repair of the sentences that still fail after
    the revision, then the full gate one final time. No loop.

    The repair may only rewrite a failing sentence to say no more than its
    quote, or delete it. services/slipstream_repair enforces that in code by
    diffing the result against the revised post; a repair that breaks the
    contract is thrown away and the post HOLDS on its original violations.
    Returns the same tuple as _factcheck_stage."""
    r2 = passes[-1]
    held = list(r2.get("violations") or ["factcheck gate failed"])
    brand_key = cfg.get("brand_key", "?")
    rep = repair_post(revised, r2, cfg, pack=pack)
    summary = _fc_summary(passes, True)
    summary["repair"] = _repair_summary(rep)
    if rep.get("skipped"):
        # Not repairable (too many failing sentences, a failure a sentence edit
        # cannot fix). Held exactly as it would have been without this step.
        logger.info("[slipstream] %s repair skipped: %s", brand_key, rep["skipped"])
        return revised, None, summary, held
    if not rep.get("ok"):
        logger.warning("[slipstream] %s repair REJECTED by the contract: %s", brand_key, rep.get("violations"))
        return revised, None, summary, held + ["repair pass rejected, original violations stand: " + v
                                               for v in rep.get("violations") or ["no usable repair"]]
    repaired = rep["post"]
    repaired["slug"] = revised["slug"]
    repaired["image_prompts"] = revised["image_prompts"]
    try:
        reassembled = reassemble(repaired) if reassemble else None
    except Exception as e:
        logger.exception("[slipstream] %s reassembly after repair failed", brand_key)
        return revised, None, summary, held + [f"repaired post could not be assembled "
                                               f"({type(e).__name__}: {e}); original violations stand"]
    fmt_violations = list(reassembled[-1] or []) if reassembled else []
    if fmt_violations:
        return repaired, None, summary, ["repaired post failed the format gate"] + fmt_violations
    try:
        r3 = _with_pack_check(run_factcheck(repaired, cfg, link_cache=cache, verdict_cache=verdicts,
                                            editor_cache=edits if edits is not None else {}),
                              repaired, cfg, pack)
    except Exception as e:
        msg = f"factcheck gate error (post HELD, never published unchecked): {type(e).__name__}: {e}"
        return repaired, None, summary, [msg]
    summary = _fc_summary(passes + [r3], True)
    summary["repair"] = _repair_summary(rep, bool(r3.get("ok")))
    if r3.get("ok"):
        logger.info("[slipstream] %s passed the final gate after repairing %d sentence(s)",
                    brand_key, len(rep.get("changes") or []))
        return repaired, reassembled, summary, []
    return repaired, None, summary, list(r3.get("violations") or ["factcheck gate failed"])


def _publish_shopify_draft(cfg: Dict[str, Any], draft: Dict[str, Any], slug: str,
                           topic: str, token: str, dry_run: bool) -> Dict[str, Any]:
    """Terminal step for the shopify_article format: create the article as a DRAFT.

    This path deliberately does NOT do the three things the repo path does after a
    successful write. There is no PR (no repo), no auto-merge (a Shopify draft is
    the ceiling; a human sets it Visible), and no social distribution (nothing is
    live to link to, and a client's social is not this engine's to fire).

    The queue topic IS checked off against the admin URL. A re-run that regenerates
    the same topic would otherwise drip near-duplicate drafts into a CLIENT's store;
    the check-off is reversible by a human unchecking the line.
    """
    brand_key = cfg.get("brand_key", "?")
    try:
        res = create_article_draft(cfg, draft, dry_run=dry_run)
    except Exception as e:
        logger.exception("[slipstream] %s shopify draft failed", brand_key)
        return {"ok": False, "held": False, "violations": [], "slug": slug,
                "error": f"shopify draft failed: {type(e).__name__}: {e}"}

    if not res.get("ok"):
        logger.warning("[slipstream] %s shopify draft NOT created: %s", brand_key, res.get("error"))
        return {"ok": False, "held": True, "violations": [res.get("error", "shopify refused")],
                "slug": slug, **res}

    if res.get("dry_run"):
        logger.info("[slipstream] %s DRY RUN ok: would create draft '%s' on blog %s (%s)",
                    brand_key, draft.get("title"), res.get("blog_id"), res.get("store"))
        return {"ok": True, "published": False, "slug": slug, "violations": [], **res}

    logger.info("[slipstream] %s DRAFT created (NOT live, Miriam-gated): %s",
                brand_key, res.get("admin_url"))
    checked = _checkoff_topic(cfg, topic, f"draft: {res.get('admin_url')}", token)
    return {"ok": True, "published": False, "slug": slug, "violations": [],
            "topic_checked_off": checked,
            "note": "Shopify DRAFT created. Not publicly visible. A human must review and "
                    "set it Visible in the Shopify admin.",
            **res}


def run_brand(
    brand_key: str,
    *,
    topic: Optional[str] = None,
    token: Optional[str] = None,
    date_str: Optional[str] = None,
    auto_merge: bool = True,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Produce a post, open a PR, then (if auto_merge) merge it once the Vercel
    build is green and distribute social. HOLDS on any gate violation or a red
    build. Returns a structured receipt (never raises for expected holds)."""
    if token is None:
        token = os.getenv("SLIPSTREAM_GH_TOKEN", "").strip()
    if not token:
        return {"ok": False, "held": True, "violations": ["SLIPSTREAM_GH_TOKEN missing"],
                "error": "no publish token"}
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        cfg = _brand_cfg(brand_key)
        fmt = cfg.get("format", "mdx")
        topic = topic or _next_topic(cfg, token)
        # RESEARCH BEFORE WRITING. For a gated brand the writer only ever sees
        # sources that code fetched and quotes that code confirmed. A research
        # failure HOLDS here; it never falls through to writing from memory.
        link_cache: Dict[str, Any] = {}
        pack, research_hold = _research_stage(cfg, topic, link_cache)
        if research_hold is not None:
            return research_hold
        research = _research_summary(pack)
        post = generate_post(cfg, topic, pack=pack) if pack is not None else generate_post(cfg, topic)
        factcheck: Optional[Dict[str, Any]] = None
        fc_violations: list = []
        # DO NOT buy images yet. Every image is a paid fal call, and the content gate
        # below can still HOLD this post -- which used to mean 3-4 images bought and
        # thrown away on every held run. Only the Shopify path needs images in hand to
        # assemble (it inlines them into the article body); every other format gates on
        # text alone, so for those we generate AFTER the gate passes.
        images: Dict[str, bytes] = {}
        if cfg.get("format") == "shopify_article":
            # Shopify must hold images to assemble, so its fact-check runs FIRST,
            # on the text alone: a post that fails it never buys an image.
            post, _, factcheck, fc_violations = _factcheck_stage(
                cfg, topic, post, pack=pack, link_cache=link_cache)
            if not fc_violations:
                images = generate_images(post["image_prompts"], cfg["business_key"])
        # Format branch: WD (ts_posts_array) serializes a Post into src/content/posts.ts;
        # P&P (shopify_article) has no repo at all and builds a Shopify article draft;
        # every other brand keeps the MDX file. `payload` is what gets published either way.
        payload = None
        tsx_files: Optional[Dict[str, str]] = None

        def _assemble(p: Dict[str, Any]):
            """(payload, tsx_files, format_violations) for post `p`. A closure so
            a revised post is re-assembled and re-gated exactly like the first."""
            if fmt == "shopify_article":
                pl, v = assemble_shopify_article(p, date_str, cfg, images)
                return pl, None, v
            if fmt == "ts_posts_array":
                pl, v = assemble_ts_posts(p, date_str, cfg, token)
                return pl, None, v
            if fmt == "tsx_post":
                # Book'd: per-post .tsx module + a splice into src/lib/blog.ts. Returns
                # the files dict directly (two files), not a single payload string.
                tf, v = assemble_tsx_post(p, date_str, cfg, token)
                return None, tf, v
            pl, v = assemble_mdx(p, date_str)
            return pl, None, v

        if fc_violations:
            violations = fc_violations            # shopify: held before images or assembly
        else:
            payload, tsx_files, violations = _assemble(post)
            # FACT-CHECK + EDITOR GATE. After the format gate passes, before any
            # image is bought and before any PR exists. One writer revision, then
            # the whole gate again; still failing = HOLD with the reasons.
            if not violations and fmt != "shopify_article":
                post, redo, factcheck, violations = _factcheck_stage(
                    cfg, topic, post, _assemble, pack=pack, link_cache=link_cache)
                if redo is not None:
                    payload, tsx_files = redo[0], redo[1]
    except QueueExhausted as e:
        # VISIBLE, not silent: an exhausted queue stops the brand producing every
        # run. Log LOUDLY (error) and return a distinct reason so the scheduler and
        # the watchdog (which reads queue depth on its own rail) both surface it.
        logger.error("[slipstream] %s QUEUE EXHAUSTED -- brand will produce NOTHING "
                     "until the queue is refilled: %s", brand_key, e)
        return {"ok": False, "held": True, "reason": "queue_exhausted",
                "violations": [str(e)], "error": "queue exhausted"}
    except Exception as e:
        logger.exception("[slipstream] %s produce failed", brand_key)
        return {"ok": False, "held": True, "violations": [f"{type(e).__name__}: {e}"],
                "error": "produce failed"}

    if violations:
        logger.warning("[slipstream] %s HELD on gate: %s", brand_key, violations)
        held = {"ok": False, "held": True, "violations": violations, "slug": post.get("slug")}
        if factcheck is not None:
            held["factcheck"] = factcheck
        if research is not None:
            held["research"] = research
        # HUMAN-FINISH MODE (on_hold: draft_pr). The held post is kept as a
        # GitHub DRAFT pull request with a NEEDS EDITOR worklist, instead of
        # being thrown away. It is still a HOLD: this function returns here, so
        # nothing below (images, merge, check-off, social) can ever run for it.
        if str(cfg.get("on_hold") or "hold").strip().lower() == "draft_pr":
            held.update(_held_draft_pr(cfg, post, violations, factcheck, research, pack,
                                       _assemble, fmt, date_str, token))
        return held

    slug = post["slug"]

    # Gate passed, so the post is going out: NOW it is worth paying for images.
    # (Shopify already generated them above, because it needs them to assemble.)
    if not images:
        try:
            images = generate_images(post["image_prompts"], cfg["business_key"])
        except Exception as e:
            logger.exception("[slipstream] %s images failed after a passing gate", brand_key)
            return {"ok": False, "held": True, "violations": [f"images: {e}"],
                    "slug": slug, "error": "image generation failed"}

    # Shopify brands (P&P) have no repo: publish means "create a DRAFT article via
    # the Admin API". Returns here, BEFORE any GitHub write path, so no branch, no
    # PR and no merge is ever attempted for a storefront brand.
    if fmt == "shopify_article":
        res = _publish_shopify_draft(cfg, payload, slug, topic, token, dry_run)
        if factcheck is not None:
            res["factcheck"] = factcheck
        if research is not None:
            res["research"] = research
        return res

    # WD writes the whole posts.ts array file (+images) INSTEAD of the mdx/social.md
    # pair. The social pack still flows to _distribute_social via post["social"].
    if fmt == "ts_posts_array":
        files: Dict[str, Any] = {cfg["posts_file"]: payload}
    elif fmt == "tsx_post":
        # {src/content/posts/<slug>.tsx: module, src/lib/blog.ts: spliced registry}
        files = dict(tsx_files or {})
    else:
        files = {f"{cfg['blog_dir']}/{slug}.mdx": payload,
                 f"{cfg['blog_dir']}/{slug}.social.md": _social_md(post)}
    # tsx_post serves images from /img/ (rewritten in the block JSX + meta.hero);
    # every other format uses /blog/.
    img_prefix = "public/img" if fmt == "tsx_post" else "public/blog"
    for name, data in images.items():
        files[f"{img_prefix}/{slug}-{name}.png"] = data

    branch = f"slipstream/{slug}-{date_str}"
    try:
        pr_url = publish_post(cfg["repo"], branch, files, f"content: {post['title']}",
                              _pr_body(post, len(images), fmt, factcheck, research), token)
    except Exception as e:
        logger.exception("[slipstream] %s publish failed", brand_key)
        return {"ok": False, "held": False, "violations": [], "slug": slug,
                "error": f"publish failed: {type(e).__name__}: {e}"}

    logger.info("[slipstream] %s PR opened %s (%d images)", brand_key, pr_url, len(images))
    result = {"ok": True, "pr_url": pr_url, "slug": slug, "image_count": len(images), "violations": []}
    if factcheck is not None:
        result["factcheck"] = factcheck
    if research is not None:
        result["research"] = research

    # auto_merge is a HARD CEILING from config: a brand with `auto_merge: false`
    # NEVER squash-merges automatically, even if a caller passes auto_merge=True.
    # This gates supervised first runs (WD) so a scheduled run can only OPEN a PR.
    effective_auto_merge = auto_merge and bool(cfg.get("auto_merge", True))
    if not effective_auto_merge:
        note = "PR opened (auto_merge off)"
        if auto_merge and not cfg.get("auto_merge", True):
            note = "PR opened (auto_merge disabled in brand config; human merges)"
        return {**result, "published": False, "note": note}

    # Auto-publish, gated on the Vercel preview build. A red build HOLDS the PR.
    # Build verification hits the Vercel API scoped to this brand's project.
    m = merge_when_green(
        cfg["repo"], pr_url, token,
        vercel_project_id=cfg.get("vercel_project_id"),
        vercel_team_id=cfg.get("vercel_team_id"),
    )
    result["published"] = m["merged"]
    if not m["merged"]:
        logger.warning("[slipstream] %s NOT merged: %s", brand_key, m.get("reason"))
        return {**result, "note": f"held for review: {m.get('reason')}"}

    domain = cfg.get("domain", "")
    live_url = f"https://{domain}/blog/{slug}" if domain else pr_url
    result["live_url"] = live_url
    result["topic_checked_off"] = _checkoff_topic(cfg, topic, live_url, token)
    result["social"] = _distribute_social(cfg, post, slug, live_url)
    logger.info("[slipstream] %s PUBLISHED LIVE %s", brand_key, live_url)
    return result
