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
from services.slipstream_generate import generate_post
from services.slipstream_github import merge_when_green, publish_post
from services.slipstream_images import generate_images
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
    return merged


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


def _pr_body(post: Dict[str, Any], image_count: int, fmt: str = "mdx") -> str:
    gate = "validate_blocks" if fmt == "ts_posts_array" else "validate_post"
    return (f"Automated Slipstream post (Railway engine).\n\n- slug: `{post['slug']}`\n"
            f"- format: `{fmt}`\n"
            f"- images: {image_count} (hero + in-body)\n- gates: passed ({gate})\n\n"
            "Vercel preview builds this PR = the build-gate. Review + merge to publish.")


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
        post = generate_post(cfg, topic)
        # DO NOT buy images yet. Every image is a paid fal call, and the content gate
        # below can still HOLD this post -- which used to mean 3-4 images bought and
        # thrown away on every held run. Only the Shopify path needs images in hand to
        # assemble (it inlines them into the article body); every other format gates on
        # text alone, so for those we generate AFTER the gate passes.
        images: Dict[str, bytes] = {}
        if cfg.get("format") == "shopify_article":
            images = generate_images(post["image_prompts"], cfg["business_key"])
        # Format branch: WD (ts_posts_array) serializes a Post into src/content/posts.ts;
        # P&P (shopify_article) has no repo at all and builds a Shopify article draft;
        # every other brand keeps the MDX file. `payload` is what gets published either way.
        payload = None
        tsx_files: Optional[Dict[str, str]] = None
        if fmt == "shopify_article":
            payload, violations = assemble_shopify_article(post, date_str, cfg, images)
        elif fmt == "ts_posts_array":
            payload, violations = assemble_ts_posts(post, date_str, cfg, token)
        elif fmt == "tsx_post":
            # Book'd: per-post .tsx module + a splice into src/lib/blog.ts. Returns
            # the files dict directly (two files), not a single payload string.
            tsx_files, violations = assemble_tsx_post(post, date_str, cfg, token)
        else:
            payload, violations = assemble_mdx(post, date_str)
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
        return {"ok": False, "held": True, "violations": violations, "slug": post.get("slug")}

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
        return _publish_shopify_draft(cfg, payload, slug, topic, token, dry_run)

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
                              _pr_body(post, len(images), fmt), token)
    except Exception as e:
        logger.exception("[slipstream] %s publish failed", brand_key)
        return {"ok": False, "held": False, "violations": [], "slug": slug,
                "error": f"publish failed: {type(e).__name__}: {e}"}

    logger.info("[slipstream] %s PR opened %s (%d images)", brand_key, pr_url, len(images))
    result = {"ok": True, "pr_url": pr_url, "slug": slug, "image_count": len(images), "violations": []}

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
