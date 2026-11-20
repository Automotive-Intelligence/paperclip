#!/usr/bin/env python3
"""Dry-run the Slipstream fact-check + editor gate on a post that already exists.

No publish side effects: this never opens a PR, never generates an image, never
writes to a repo. It reads one file, fetches the links in it, and prints the
verdict the engine would reach.

    python3 scripts/slipstream_gate_check.py POST --brand BRAND [--llm] [--json]

POST may be:
  - a GitHub PR diff saved to disk (`gh pr diff 78 -R owner/repo > post.diff`):
    the added .mdx file (plus its .social.md) or the Post object added to
    src/content/posts.ts is reconstructed from the + lines;
  - an .mdx / .md post file (frontmatter + body);
  - a .json file holding the generator's raw post object.

BRAND is a key in config/slipstream_brands.yaml (its domain, banned-content
rules and facts file are used).

By default only the deterministic checks run (links, figures, banned content),
which cost nothing. --llm also runs the verifier and editor on the configured
verifier model (SLIPSTREAM_VERIFIER_MODEL), which is a paid call and needs
ANTHROPIC_API_KEY in the environment (for example under `doppler run --`).

Exit code: 0 = would pass, 1 = would be HELD, 2 = could not read the input.

END-TO-END DRY RUN (research -> write -> gate), no post file needed:

    doppler run -p paperclip -c prd -- \\
        python3 scripts/slipstream_gate_check.py --write --brand BRAND [--topic "..."] [--out DIR]

This runs the REAL engine path (run_brand) for one brand and one topic with
every publishing side effect replaced: no image is generated, no branch or PR
is opened, no Shopify draft is created, nothing is merged, no social is
scheduled and the topic queue is only READ (never checked off). Without --topic
it uses the next unchecked topic in the brand's queue. These are paid model
calls (research, writer, verifier, editor). --out saves the source pack, the
post and the full receipt as JSON. Secrets come from the environment only.

REPAIR A SAVED POST (no research, no writer, no revision):

    doppler run -p paperclip -c prd -- \\
        python3 scripts/slipstream_gate_check.py --from-receipt RECEIPT.json \\
            [--out DIR] [--cap 1.00] [--spent 0.00]

RECEIPT.json is a file an earlier --write run saved. This loads its post (after
the revision), its source pack, brand and topic, then:
  1. re-fetches every pack source and re-confirms every quote with code (free);
     anything that no longer loads or no longer holds its quote is dropped;
  2. rebuilds the sentence-level failures: the code checks run fresh (free), the
     claim verdicts come from the saved receipt, and the editor runs once
     (a small paid call) so every offending line is named;
  3. runs the targeted repair pass (a small paid call);
  4. runs the final gate (paid; verified sentences are carried forward).
Every paid call is estimated first and refused if --spent plus the run so far
plus the estimate would cross --cap. Nothing is published.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _added_files(diff: str) -> Dict[str, str]:
    """{path: text of the + lines} for every file in a unified diff."""
    files: Dict[str, List[str]] = {}
    current = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            current = path[2:] if path.startswith("b/") else path
            files[current] = []
        elif line.startswith(("--- ", "diff --git", "@@", "index ", "new file", "Binary ")):
            continue
        elif current and line.startswith("+"):
            files[current].append(line[1:])
    return {k: "\n".join(v) for k, v in files.items() if k != "/dev/null"}


def _frontmatter(mdx: str) -> tuple:
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", mdx, re.S)
    if not m:
        return {}, mdx
    fm: Dict[str, str] = {}
    for line in m.group(1).splitlines():
        km = re.match(r"^(\w+)\s*:\s*(.*)$", line)
        if km:
            v = km.group(2).strip()
            if len(v) >= 2 and v[0] == v[-1] == '"':
                try:
                    v = json.loads(v)
                except ValueError:
                    v = v[1:-1]
            fm[km.group(1)] = v
    return fm, m.group(2)


def _social_from_md(md: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for name, body in re.findall(r"^## +(\w+)\s*\n(.*?)(?=^## |\Z)", md or "", re.S | re.M):
        out[name.lower()] = body.strip()
    return out


def post_from_mdx(mdx: str, social_md: str = "") -> Dict[str, Any]:
    fm, body = _frontmatter(mdx)
    return {"title": fm.get("title", ""), "description": fm.get("description", ""),
            "slug": fm.get("slug", ""), "body_mdx": body, "social": _social_from_md(social_md)}


def post_from_ts(added: str) -> Dict[str, Any]:
    """Rebuild the Post object the engine spliced into posts.ts. The engine's
    serializer emits bare keys at line start and JSON-escaped double-quoted
    strings, so quoting the keys and dropping trailing commas yields JSON."""
    start = added.find("{")
    if start < 0:
        raise ValueError("no object literal in the added lines")
    text = re.sub(r"^(\s*)([A-Za-z_]\w*):", r'\1"\2":', added[start:], flags=re.M)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    obj, _ = json.JSONDecoder().raw_decode(text)
    return obj


def load_post(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read()
    if path.endswith(".json"):
        return json.loads(raw)
    if raw.startswith("diff --git") or "\n+++ " in raw[:4000]:
        files = _added_files(raw)
        mdx = next((t for p, t in files.items() if p.endswith(".mdx")), None)
        if mdx is not None:
            social = next((t for p, t in files.items() if p.endswith(".social.md")), "")
            return post_from_mdx(mdx, social)
        ts = next((t for p, t in files.items() if p.endswith(".ts")), None)
        if ts is not None:
            return post_from_ts(ts)
        raise ValueError("diff contains no .mdx post and no posts .ts file")
    return post_from_mdx(raw)


# USD per million tokens (input, output), from Anthropic's published price table
# as cached 2026-11-03. Web search is billed per request on top of tokens.
_PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0),
           "claude-sonnet-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50)}
_WEB_SEARCH_USD = 10.0 / 1000


def cost_report(usage_log: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Approximate spend for the calls in gen.USAGE_LOG, grouped by surface."""
    rows: Dict[str, Dict[str, Any]] = {}
    total = 0.0
    unpriced: List[str] = []
    for u in usage_log:
        model = str(u.get("model") or "")
        price = next((v for k, v in _PRICES.items() if model.startswith(k)), None)
        if price is None:
            unpriced.append(model)
            price = (0.0, 0.0)
        tokens_in = (u.get("input_tokens", 0) + u.get("cache_creation_tokens", 0)
                     + u.get("cache_read_tokens", 0))
        usd = (tokens_in * price[0] + u.get("output_tokens", 0) * price[1]) / 1e6 \
            + u.get("web_searches", 0) * _WEB_SEARCH_USD
        key = f"{u.get('surface')} ({model})"
        row = rows.setdefault(key, {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                                    "web_searches": 0, "usd": 0.0})
        row["calls"] += 1
        row["input_tokens"] += tokens_in
        row["output_tokens"] += u.get("output_tokens", 0)
        row["web_searches"] += u.get("web_searches", 0)
        row["usd"] += usd
        total += usd
    return {"by_surface": rows, "total_usd": round(total, 4), "unpriced_models": sorted(set(unpriced))}


# Expected OUTPUT tokens per call, by ledger surface, for the pre-call estimate.
_EXPECTED_OUT_BY_SURFACE = {"slipstream": 12000, "slipstream_factcheck": 9000,
                            "slipstream_research": 6000, "slipstream_repair": 6000}
_SEARCH_ESTIMATE_USD = 0.30
_RUN_RESERVE_USD = 2.50          # do not START a run unless this much is left under the cap


def run_dry(brand: str, topic: str = "", research_overrides: Dict[str, Any] = None,
            budget: "Budget" = None) -> Dict[str, Any]:
    """research -> write -> gate (one revision, repair) through the real
    run_brand, with every publishing side effect replaced. Returns everything
    the run produced. `research_overrides` changes the brand's research config
    for THIS run only (extra blocked_domains are added, other keys replace).
    With a `budget`, every model call is estimated first and refused if it
    would cross the cap."""
    from unittest import mock
    from services import slipstream_engine as se
    from services import slipstream_generate as gen
    from services import slipstream_research as rs

    seen: Dict[str, Any] = {"posts": []}
    real_pack, real_generate, real_revise = se.build_source_pack, se.generate_post, se.revise_post

    def _pack(*a, **kw):
        seen["pack"] = real_pack(*a, **kw)
        return seen["pack"]

    def _generate(*a, **kw):
        post = real_generate(*a, **kw)
        seen["posts"].append({"stage": "draft", "post": post})
        return post

    def _revise(*a, **kw):
        seen["violations_sent_to_writer"] = list(a[3]) if len(a) > 3 else []
        post = real_revise(*a, **kw)
        seen["posts"].append({"stage": "revision", "post": post})
        return post

    real_repair = se.repair_post

    def _repair(*a, **kw):
        result = real_repair(*a, **kw)
        if result.get("ok"):
            seen["posts"].append({"stage": "repair", "post": result["post"]})
        return result

    def _no_images(prompts, key):
        return {str((p or {}).get("name")): b"" for p in prompts or []}

    def _no_publish(repo, branch, files, title, body, token):
        seen["pr_body"] = body
        seen["would_commit"] = sorted(files)
        return "dry-run://no-pr-opened"

    def _forbidden(name):
        def _f(*a, **kw):
            raise RuntimeError(f"dry run must never call {name}")
        return _f

    real_cfg, real_once, real_search = se._brand_cfg, gen._llm_json_once, rs.default_search

    def _cfg(key):
        cfg = real_cfg(key)
        if research_overrides and key == brand:
            r = dict(cfg.get("research") or {})
            for k, v in research_overrides.items():
                r[k] = list(r.get(k) or []) + [d for d in v if d not in (r.get(k) or [])] \
                    if k == "blocked_domains" else v
            cfg = {**cfg, "research": r}
        return cfg

    def _once(system, user, **kw):
        if budget is not None:
            model = kw.get("model") or gen._MODEL
            surface = kw.get("surface", "slipstream")
            price = next((v for k, v in _PRICES.items() if model.startswith(k)), (4.0, 20.0))
            out_tokens = min(int(kw.get("max_tokens") or gen._MAX_TOKENS),
                             _EXPECTED_OUT_BY_SURFACE.get(surface, 9000))
            est = ((len(system) + len(user)) / 3.5 * price[0] + out_tokens * price[1]) / 1e6
            budget.check(surface, est)
        return real_once(system, user, **kw)

    def _search(*a, **kw):
        if budget is not None:
            budget.check("search", _SEARCH_ESTIMATE_USD)
        return real_search(*a, **kw)

    usage_start = len(gen.USAGE_LOG)
    with mock.patch.object(se, "_brand_cfg", _cfg), \
         mock.patch.object(gen, "_llm_json_once", _once), \
         mock.patch.object(rs, "default_search", _search), \
         mock.patch.object(se, "build_source_pack", _pack), \
         mock.patch.object(se, "generate_post", _generate), \
         mock.patch.object(se, "revise_post", _revise), \
         mock.patch.object(se, "repair_post", _repair), \
         mock.patch.object(se, "generate_images", _no_images), \
         mock.patch.object(se, "publish_post", _no_publish), \
         mock.patch.object(se, "create_article_draft",
                           lambda cfg, draft, dry_run=False: {"ok": True, "dry_run": True}), \
         mock.patch.object(se, "_checkoff_topic", _forbidden("_checkoff_topic")), \
         mock.patch.object(se, "merge_when_green", _forbidden("merge_when_green")), \
         mock.patch.object(se, "_distribute_social", _forbidden("_distribute_social")):
        if not topic:
            cfg = se._brand_cfg(brand)
            topic = se._next_topic(cfg, os.getenv("SLIPSTREAM_GH_TOKEN", "").strip())
        receipt = se.run_brand(brand, topic=topic, auto_merge=False, dry_run=True)
        cfg_used = _cfg(brand)

    usage = list(gen.USAGE_LOG[usage_start:])
    stopped = "BudgetStop" in json.dumps(receipt, default=str)
    return {"brand": brand, "topic": topic, "receipt": receipt, "pack": seen.get("pack"),
            "posts": seen["posts"], "pr_body": seen.get("pr_body"),
            "violations_sent_to_writer": seen.get("violations_sent_to_writer"),
            "cost": cost_report(usage), "usage": usage, "stopped_by_cap": stopped, "cfg": cfg_used}


def dry_run_write(brand: str, topic: str = "", out_dir: str = "") -> int:
    """run_dry for one brand, printed for a person. Returns the exit code."""
    from services import slipstream_generate as gen
    gen.USAGE_LOG.clear()
    run = run_dry(brand, topic)
    receipt, topic, seen, cost = run["receipt"], run["topic"], run, run["cost"]
    passed = bool(receipt.get("ok"))
    research = receipt.get("research") or {}
    print(f"BRAND   {brand}")
    print(f"TOPIC   {topic}")
    print(f"VERDICT {'PASS' if passed else 'HOLD'}"
          + (f"  reason={receipt.get('reason')}" if receipt.get("reason") else "")
          + (f"  error={receipt.get('error')}" if receipt.get("error") else ""))
    print(f"\nRESEARCH mode={research.get('mode')} searches={research.get('searches')} "
          f"candidates={research.get('candidates')} results_seen={research.get('search_results_seen')} "
          f"fallback_used={research.get('search_fallback_used')} retried={research.get('retried')} "
          f"usable={research.get('usable_sources')} quotes_discarded={research.get('quotes_discarded')}")
    for b in research.get("brand_pages") or []:
        print(f"  FIRST-PARTY {b['path']} quotes={b['quotes']}")
    if research.get("brand_quotes_banned"):
        print(f"  FIRST-PARTY quotes withheld by a banned rule: {research['brand_quotes_banned']}")
    for s in research.get("sources") or []:
        print(f"  SOURCE  {'primary ' if s.get('primary') else ''}{s['url']}  "
              f"[{s.get('publisher')}] quotes={s.get('quotes')} figures={s.get('figures')}")
        for sh in s.get("second_hand") or []:
            print(f"    SECOND-HAND {sh}")
    for d in research.get("dropped") or []:
        print(f"  DROPPED {d['url']}  ({d['reason']})")
    print(f"  INTERNAL {', '.join(research.get('internal_links') or []) or '(none verified)'}")
    fcr = receipt.get("factcheck") or {}
    print(f"\nGATE    ran={fcr.get('ran')} passed={fcr.get('passed')} revised={fcr.get('revised')} "
          f"passes={fcr.get('passes')} llm_ran={fcr.get('llm_ran')} "
          f"first_pass_violations={fcr.get('first_pass_violations')}")
    print("COUNTS  " + ", ".join(f"{k}={v}" for k, v in (fcr.get("counts") or {}).items()))
    if seen.get("violations_sent_to_writer"):
        print(f"\nFIRST-PASS VIOLATIONS sent to the writer ({len(seen['violations_sent_to_writer'])})")
        for i, v in enumerate(seen["violations_sent_to_writer"], 1):
            print(f"  {i}. {v}")
    rep_ = fcr.get("repair")
    if rep_:
        print(f"\nREPAIR  ran={rep_.get('ran')} applied={rep_.get('applied')} targets={rep_.get('targets')} "
              f"final_gate_passed={rep_.get('final_gate_passed')}")
        if rep_.get("skipped"):
            print(f"  SKIPPED {rep_['skipped']}")
        for v in rep_.get("contract_violations") or []:
            print(f"  CONTRACT {v}")
        for c in rep_.get("changes") or []:
            print(f"  {c['action'].upper()} [{c['where']}] rules={','.join(c.get('rules') or [])}")
            print(f"    - {c['before']}")
            if c.get("after"):
                print(f"    + {c['after']}")
    print(f"\nFINAL VIOLATIONS ({len(receipt.get('violations') or [])})")
    for i, v in enumerate(receipt.get("violations") or [], 1):
        print(f"  {i}. {v}")
    for c in fcr.get("claims") or []:
        print(f"  [{c['verdict']}] {c['claim']}  <- {c['source'] or 'no source'}")
    print(f"\nCOST    about ${cost['total_usd']:.2f}")
    for k, row in cost["by_surface"].items():
        print(f"  {k}: calls={row['calls']} in={row['input_tokens']} out={row['output_tokens']} "
              f"searches={row['web_searches']} ${row['usd']:.3f}")
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, f"{brand}.json"), "w", encoding="utf-8") as fh:
            json.dump({k: v for k, v in run.items() if k != "cfg"}, fh, indent=2, default=str)
        print(f"\nSAVED   {os.path.join(out_dir, brand + '.json')}")
    return 0 if passed else 1


# ----------------------------------------------------------------------------
# --from-receipt: repair a saved, revised post
# ----------------------------------------------------------------------------

class BudgetStop(Exception):
    """A paid call was refused because it would cross the spend cap."""


# Expected OUTPUT tokens per call kind (reasoning included), from the 2026-11-15
# runs. Used only to estimate a call before it is made.
_EXPECTED_OUT = {"verifier": 6000, "editor": 1500, "repair": 5000}


class Budget:
    """Estimates each paid call before it runs and refuses one that would cross
    the cap. Measured spend comes from gen.USAGE_LOG, not from the estimates."""

    def __init__(self, cap: float, spent_before: float = 0.0):
        self.cap, self.spent_before, self.log = float(cap), float(spent_before), []

    def measured(self) -> float:
        from services import slipstream_generate as gen
        return cost_report(gen.USAGE_LOG)["total_usd"]

    def total(self) -> float:
        return self.spent_before + self.measured()

    def estimate(self, kind: str, model: str, system: str, user: str) -> float:
        price = next((v for k, v in _PRICES.items() if model.startswith(k)), (4.0, 20.0))
        tokens_in = (len(system) + len(user)) / 3.5          # conservative chars per token
        return (tokens_in * price[0] + _EXPECTED_OUT[kind] * price[1]) / 1e6

    def check(self, kind: str, estimate: float) -> None:
        """Refuse (raise) if `estimate` would take the round over the cap."""
        if self.total() + estimate > self.cap:
            self.log.append({"kind": kind, "estimate": round(estimate, 3), "allowed": False})
            raise BudgetStop(f"{kind} call estimated at ${estimate:.2f} would take the round to "
                             f"${self.total() + estimate:.2f}, over the ${self.cap:.2f} cap")
        self.log.append({"kind": kind, "estimate": round(estimate, 3), "allowed": True})

    def guard(self, kind: str, model: str, call):
        def _call(system: str, user: str):
            est = self.estimate(kind, model, system, user)
            if self.total() + est > self.cap:
                self.log.append({"kind": kind, "estimate": round(est, 3), "allowed": False})
                raise BudgetStop(f"{kind} call estimated at ${est:.2f} would take the round to "
                                 f"${self.total() + est:.2f}, over the ${self.cap:.2f} cap")
            before = self.measured()
            out = call(system, user)
            self.log.append({"kind": kind, "estimate": round(est, 3), "allowed": True,
                             "measured": round(self.measured() - before, 4)})
            return out
        return _call


def load_receipt(path: str) -> Dict[str, Any]:
    """Brand, topic, pack, the post as it stood after the last gate pass, and
    that pass's verdicts, from a file an earlier --write run saved."""
    with open(path, "r", encoding="utf-8") as fh:
        d = json.load(fh)
    posts = d.get("posts") or []
    if not posts or not isinstance(posts[-1].get("post"), dict):
        raise ValueError("receipt holds no post")
    if posts[-1].get("stage") == "repair":
        raise ValueError("receipt's last post is already a repaired one")
    receipt = d.get("receipt") or {}
    fcr = receipt.get("factcheck") or {}
    if not d.get("brand") or not isinstance(d.get("pack"), dict):
        raise ValueError("receipt has no brand or no source pack")
    if len(posts) > 1 and not fcr.get("revised"):
        raise ValueError("receipt has a revision but its gate result is for the draft")
    return {"brand": d["brand"], "topic": d.get("topic") or "", "pack": d["pack"],
            "post": posts[-1]["post"], "stage": posts[-1].get("stage"),
            "claims": list(fcr.get("claims") or []),
            "violations": list(receipt.get("violations") or [])}


def recheck_pack(pack: Dict[str, Any], cfg: Dict[str, Any], http_get, cache: Dict[str, Any]) -> tuple:
    """Re-fetch every pack source with the gate's fetcher and re-confirm every
    quote with code. Returns (pack as it stands today, [what was dropped])."""
    from services import slipstream_factcheck as fc
    from services import slipstream_research as rs
    new = json.loads(json.dumps(pack))
    dropped: List[str] = []

    def still(url: str, quotes: List[Dict[str, Any]], window: int, internal: bool) -> List[Dict[str, Any]]:
        if url not in cache:
            cache[url] = fc.check_link(url, http_get, internal=internal)
        f = cache[url]
        why = rs.usable_page(f) if not internal else (None if f.state == "live" and f.status == 200 else
                                                      f"does not return 200 ({f.detail or f.status})")
        if why:
            dropped.append(f"source no longer usable ({why}): {url}")
            return []
        page = fc._norm(f.text[:window])
        kept = []
        for q in quotes:
            if fc._norm(q["text"]) in page:
                kept.append(q)
            else:
                dropped.append(f'quote is no longer on the page ({url}): "{fc._short(q["text"], 120)}"')
        if quotes and not kept:
            dropped.append(f"source dropped, none of its quotes is still on the page: {url}")
        return kept

    sources = []
    for s in new.get("sources") or []:
        s["quotes"] = still(s["url"], s.get("quotes") or [], fc._PROMPT_SOURCE_CHARS, False)
        if s["quotes"]:
            sources.append(s)
    new["sources"] = sources
    pages = []
    for b in new.get("brand_pages") or []:
        b["quotes"] = still(b["url"], b.get("quotes") or [], rs._BRAND_PAGE_CHARS, True)
        if b["quotes"]:
            pages.append(b)
    new["brand_pages"] = pages
    domain = str(cfg.get("domain") or "").lower()
    domain = domain[4:] if domain.startswith("www.") else domain
    links = []
    for path in new.get("internal_links") or []:
        url = f"https://{domain}{path}"
        if url not in cache:
            cache[url] = fc.check_link(url, http_get, internal=True)
        if cache[url].state == "live" and cache[url].status == 200:
            links.append(path)
        else:
            dropped.append(f"internal link no longer returns 200: {path}")
    new["internal_links"] = links
    new["usable_sources"] = rs.usable_source_count(sources)
    return new, dropped


def saved_claim_failures(post: Dict[str, Any], claims: List[Dict[str, Any]],
                         violations: List[str]) -> tuple:
    """The saved pass's claim verdicts as structured failures, each tied to its
    sentence. Returns (failures, aligned). `aligned` is False when a saved
    verdict cannot be matched to the sentence it names (then nothing saved is
    trusted and the caller must judge the post fresh)."""
    from services import slipstream_factcheck as fc
    units, _ = fc.extract_post(post)
    by_id = {u.uid: u for u in units}
    out: List[Dict[str, Any]] = []
    for c in claims:
        if c.get("verdict") in ("SUPPORTED", "NOT_A_CLAIM"):
            if c.get("sentence_id") and c["sentence_id"] not in by_id:
                return [], False
            continue
        unit = by_id.get(c.get("sentence_id") or "")
        if unit is None:
            return [], False
        needle = f'"{fc._short(unit.text)}"'
        match = next((v for v in violations if needle in v and not v.startswith("editor:")), None)
        if match is None:
            return [], False
        fc._fail(out, unit, "claim", match, verdict=c.get("verdict"), source=c.get("source") or "",
                 claim=c.get("claim") or "")
    return out, True


def _assembler(cfg: Dict[str, Any], date_str: str):
    """(payload, tsx_files, format_violations) for a post, the same branch
    run_brand takes. Read-only: the block formats read the site's current file."""
    from services import slipstream_engine as se
    fmt = cfg.get("format", "mdx")
    token = os.getenv("SLIPSTREAM_GH_TOKEN", "").strip()

    def _assemble(p: Dict[str, Any]):
        if fmt == "ts_posts_array":
            pl, v = se.assemble_ts_posts(p, date_str, cfg, token)
            return pl, None, v
        if fmt == "tsx_post":
            tf, v = se.assemble_tsx_post(p, date_str, cfg, token)
            return None, tf, v
        if fmt == "shopify_article":
            return None, None, []                 # assembled only with images in hand
        pl, v = se.assemble_mdx(p, date_str)
        return pl, None, v
    return _assemble


def readable_markdown(post: Dict[str, Any], cfg: Dict[str, Any], repair: Dict[str, Any]) -> str:
    """The final post as a file a person can read: title, description, body,
    sources, and what the repair pass changed."""
    from services import slipstream_factcheck as fc
    domain = cfg.get("domain", "")
    out = [f"# {post.get('title', '')}", "", f"_{post.get('description', '')}_", "",
           f"Brand: {cfg.get('brand_key')} ({domain}). Slug: `{post.get('slug')}`. "
           "NOT PUBLISHED. This is a dry-run result that passed the fact-check gate.", ""]
    links: List[str] = []
    if isinstance(post.get("body_mdx"), str):
        body = post["body_mdx"]
        body = re.sub(r"<AnswerFirst>(.*?)</AnswerFirst>", r"> **Answer:** \1", body, flags=re.S)
        body = re.sub(r'<EntityDefinition term="([^"]*)">(.*?)</EntityDefinition>', r"**\1:** \2", body, flags=re.S)
        body = re.sub(r"<Callout>(.*?)</Callout>", r"> **Note:** \1", body, flags=re.S)
        body = re.sub(r"<PullQuote>(.*?)</PullQuote>", r"> \1", body, flags=re.S)
        body = re.sub(r"<KeyTakeaway>(.*?)</KeyTakeaway>", r"> **Key takeaway:** \1", body, flags=re.S)
        body = re.sub(r'<StatRow\b[^>]*?value="([^"]*)"[^>]*?label="([^"]*)"[^>]*?source="([^"]*)"[^>]*?href="([^"]*)"[^>]*/>',
                      r"**\1** \2 ([\3](\4))", body)
        body = re.sub(r'<img\b[^>]*?alt="([^"]*)"[^>]*/?>', r"_[image: \1]_", body)
        out.append(body)
    else:
        for b in post.get("body") or []:
            t = b.get("type")
            if t == "answer":
                out += [f"> **Answer:** {b.get('text', '')}", ""]
            elif t in ("h2", "h3"):
                out += [f"{'##' if t == 'h2' else '###'} {b.get('text', '')}", ""]
            elif t in ("p",):
                out += [str(b.get("text", "")), ""]
            elif t == "quote":
                out += [f"> {b.get('text', '')}", ""]
            elif t == "definition":
                out += [f"**{b.get('term', '')}:** {b.get('text', '')}", ""]
            elif t == "callout":
                out += [f"> **{b.get('title', 'Note')}:** {b.get('text', '')}", ""]
            elif t == "ul":
                out += [f"- {i}" for i in b.get("items") or []] + [""]
            elif t == "table":
                heads = b.get("headers") or []
                out += ["| " + " | ".join(str(h) for h in heads) + " |", "|" + "---|" * len(heads)]
                out += ["| " + " | ".join(str(c) for c in row) + " |" for row in b.get("rows") or []] + [""]
            elif t == "image":
                out += [f"_[image: {b.get('alt', '')}]_ {b.get('caption', '')}".rstrip(), ""]
            elif t == "stat":
                out += [f"**{b.get('value', '')}** {b.get('label', '')} ([{b.get('source', '')}]({b.get('href', '')}))", ""]
            elif t in ("links", "sources"):
                out += [f"**{b.get('title', t.title())}**"]
                out += [f"- [{i.get('label', '')}]({i.get('href', '')})" for i in b.get("items") or []] + [""]
        if post.get("faq"):
            out += ["## FAQ", ""]
            for qa in post["faq"]:
                out += [f"**{qa.get('q', '')}**", "", str(qa.get("a", "")), ""]
    units, _ = fc.extract_post(post)
    for u in units:
        for h in u.links:
            r = fc.resolve_link(h, domain)
            if r and not r[1] and r[0] not in links:
                links.append(r[0])
    out += ["", "## Sources cited (every one fetched and quote-checked by code)", ""]
    out += [f"- {u}" for u in links] or ["- none"]
    social = post.get("social") or {}
    if social:
        out += ["", "## Social drafts", ""]
        for k, v in social.items():
            out += [f"**{k}:** {v}", ""]
    out += ["", "## What the repair pass changed", ""]
    for c in (repair or {}).get("changes") or []:
        out += [f"- {c['action'].upper()} ({c['where']}; rule: {', '.join(c.get('rules') or [])})",
                f"  - before: {c['before']}"]
        if c.get("after"):
            out.append(f"  - after: {c['after']}")
    if not (repair or {}).get("changes"):
        out.append("- nothing")
    return "\n".join(out) + "\n"


def repair_from_receipt(path: str, out_dir: str = "", cap: float = 1.0, spent: float = 0.0,
                        http_get=None) -> int:
    """Load a saved post and run ONLY: pack re-check, failure rebuild, repair,
    final gate. No research, no writer, no revision, no images, no publish."""
    from datetime import datetime, timezone
    from unittest import mock
    from services import slipstream_engine as se
    from services import slipstream_factcheck as fc
    from services import slipstream_generate as gen
    from services import slipstream_repair as rp

    saved = load_receipt(path)
    brand, post = saved["brand"], saved["post"]
    cfg = se._brand_cfg(brand)
    http_get = http_get or fc.default_http_get
    gen.USAGE_LOG.clear()
    budget = Budget(cap, spent)
    cache: Dict[str, Any] = {}
    verdicts: Dict[Any, Any] = {}

    pack, pack_dropped = recheck_pack(saved["pack"], cfg, http_get, cache)
    words_before = rp._body_words(post)

    # Sentence-level failures of the saved post, as cheaply as honesty allows.
    det = fc.run_gate(post, cfg, http_get=http_get, link_cache=cache, use_llm=False)
    det = se._with_pack_check(det, post, cfg, pack)
    failures = list(det.get("failures") or [])
    violations = list(det.get("violations") or [])
    claim_failures, aligned = saved_claim_failures(post, saved["claims"], saved["violations"])
    units, _ = fc.extract_post(post)
    model = fc.verifier_model()
    stopped = None
    result: Dict[str, Any] = {}
    try:
        if not aligned:
            raise ValueError("saved verdicts do not line up with the saved post; judge it with --write instead")
        fc.remember_verdicts(verdicts, units, saved["claims"])
        failures += claim_failures
        violations += [f["violation"] for f in claim_failures]
        editor_llm = budget.guard("editor", model, fc.default_llm(model))
        block = isinstance(post.get("body"), list)
        ed_sink: List[Dict[str, Any]] = []
        ed_v, _ed = fc.evaluate_editor(
            editor_llm(fc.EDITOR_SYSTEM, fc.build_editor_prompt(
                post, units, fc.load_facts(cfg), fc.sourced_sentence_ids(saved["claims"]) if block else None)),
            units, ed_sink)
        failures += ed_sink
        violations += ed_v
        r2 = {"ok": not violations, "violations": fc._dedupe(violations), "error": None,
              "claims": saved["claims"], "editor": _ed, "links": det.get("links") or [],
              "model": model, "llm_ran": True, "failures": failures,
              "counts": {**(det.get("counts") or {}), "claims": len(saved["claims"]),
                         "violations": len(fc._dedupe(violations))}}
        result["before_repair"] = {"violations": r2["violations"],
                                   "failures": [{k: f[k] for k in ("uid", "where", "rule", "text")} for f in failures]}
        if r2["ok"]:
            final_post, redo, summary, final_v = post, None, fc.summarize([r2], True), []
        else:
            real_gate_llm, real_repair_llm = fc.default_llm, rp.default_llm

            def gate_llm(m):
                inner = real_gate_llm(m)
                return lambda system, user: budget.guard(
                    "verifier" if system == fc.VERIFIER_SYSTEM else "editor", m, inner)(system, user)

            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            with mock.patch.object(fc, "default_llm", gate_llm), \
                 mock.patch.object(rp, "default_llm",
                                   lambda: budget.guard("repair", gen._MODEL, real_repair_llm())):
                final_post, redo, summary, final_v = se._repair_stage(
                    cfg, post, [r2], _assembler(cfg, date_str), pack, cache, verdicts)
    except BudgetStop as e:
        stopped = str(e)
        final_post, redo, summary, final_v = post, None, {}, ["STOPPED before finishing: " + stopped]

    repair = (summary or {}).get("repair") or {}
    blob = json.dumps(repair, default=str)
    if ("BudgetStop" in blob or any("BudgetStop" in v for v in final_v)) and not stopped:
        stopped = "a paid call inside the repair stage was refused by the spend cap"
    passed = not final_v and not stopped
    cost = cost_report(gen.USAGE_LOG)
    words_after = rp._body_words(final_post)
    print(f"BRAND   {brand}")
    print(f"TOPIC   {saved['topic']}")
    print(f"SOURCE  {path} (post stage: {saved['stage']})")
    print(f"VERDICT {'PASS' if passed else ('STOPPED (spend cap)' if stopped else 'HOLD')}")
    print(f"\nPACK RECHECK {len(pack.get('sources') or [])} source(s) still usable, "
          f"{len(pack.get('brand_pages') or [])} first-party page(s), {len(pack_dropped)} thing(s) dropped")
    for d in pack_dropped:
        print(f"  DROPPED {d}")
    print(f"\nBEFORE REPAIR ({len(result.get('before_repair', {}).get('violations', []))} violations)")
    for i, v in enumerate(result.get("before_repair", {}).get("violations", []), 1):
        print(f"  {i}. {v}")
    print(f"\nREPAIR  ran={repair.get('ran')} applied={repair.get('applied')} targets={repair.get('targets')} "
          f"final_gate_passed={repair.get('final_gate_passed')}")
    if repair.get("skipped"):
        print(f"  SKIPPED {repair['skipped']}")
    for v in repair.get("contract_violations") or []:
        print(f"  CONTRACT {v}")
    for c in repair.get("changes") or []:
        print(f"  {c['action'].upper()} [{c['where']}] rules={','.join(c.get('rules') or [])}")
        print(f"    - {c['before']}")
        if c.get("after"):
            print(f"    + {c['after']}")
    counts = (summary or {}).get("counts") or {}
    print("\nFINAL COUNTS " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    print(f"WORDS   body {words_before} before, {words_after} after")
    print(f"\nFINAL VIOLATIONS ({len(final_v)})")
    for i, v in enumerate(final_v, 1):
        print(f"  {i}. {v}")
    print(f"\nCOST    measured about ${cost['total_usd']:.2f} this run; round total ${budget.total():.2f} "
          f"of ${cap:.2f} cap")
    for k, row in cost["by_surface"].items():
        print(f"  {k}: calls={row['calls']} in={row['input_tokens']} out={row['output_tokens']} ${row['usd']:.3f}")
    for b in budget.log:
        print(f"  ESTIMATE {b['kind']}: ${b['estimate']:.3f} "
              + (f"measured ${b.get('measured', 0):.3f}" if b["allowed"] else "REFUSED"))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, f"{brand}.json"), "w", encoding="utf-8") as fh:
            json.dump({"brand": brand, "topic": saved["topic"], "from_receipt": path, "passed": passed,
                       "stopped": stopped, "pack_dropped": pack_dropped, "pack": pack,
                       "before_repair": result.get("before_repair"), "factcheck": summary,
                       "final_violations": final_v, "post_before": post, "post_after": final_post,
                       "words_before": words_before, "words_after": words_after,
                       "cost": cost, "budget": budget.log, "usage": list(gen.USAGE_LOG)},
                      fh, indent=2, default=str)
        print(f"\nSAVED   {os.path.join(out_dir, brand + '.json')}")
        if passed:
            payload, tsx_files = (redo[0], redo[1]) if redo else (None, None)
            with open(os.path.join(out_dir, f"{brand}-FINAL.md"), "w", encoding="utf-8") as fh:
                fh.write(readable_markdown(final_post, cfg, repair))
            with open(os.path.join(out_dir, f"{brand}-final.json"), "w", encoding="utf-8") as fh:
                json.dump({"brand": brand, "topic": saved["topic"], "format": cfg.get("format", "mdx"),
                           "published": False, "post": final_post, "assembled_payload": payload,
                           "assembled_files": tsx_files, "factcheck": summary, "pack": pack},
                          fh, indent=2, default=str)
            print(f"FINAL   {os.path.join(out_dir, brand + '-FINAL.md')}")
            print(f"FINAL   {os.path.join(out_dir, brand + '-final.json')}")
    return 0 if passed else 1


# ----------------------------------------------------------------------------
# --bank: produce a set of researched drafts and index them
# ----------------------------------------------------------------------------

def _bank_row(run: Dict[str, Any], path_md: str) -> Dict[str, Any]:
    receipt = run["receipt"]
    fcr = receipt.get("factcheck") or {}
    counts = fcr.get("counts") or {}
    research = receipt.get("research") or {}
    sources = research.get("sources") or []
    failing = {f["text"] for f in fcr.get("failures") or [] if f.get("text")}
    if receipt.get("ok"):
        verdict = "PASS"
    elif run.get("stopped_by_cap"):
        verdict = "STOPPED (spend cap)"
    elif receipt.get("reason"):
        verdict = f"HOLD ({receipt['reason']}, no draft)"
    elif not run.get("posts"):
        verdict = "HOLD (no draft)"
    else:
        verdict = "HOLD (draft banked, needs editor)"
    return {"brand": run["brand"], "topic": run["topic"], "verdict": verdict,
            "claims": f"{counts.get('claims_supported', 0)} of {counts.get('claims', 0)}",
            "failing_sentences": len(failing), "violations": len(receipt.get("violations") or []),
            "sources": f"{len(sources)} ({sum(1 for s in sources if s.get('primary'))} primary)",
            "cost": run["cost"]["total_usd"], "path": path_md if run.get("posts") else "",
            "repair": "applied" if (fcr.get("repair") or {}).get("applied") else
                      ("skipped" if (fcr.get("repair") or {}).get("skipped") else
                       ("rejected" if (fcr.get("repair") or {}).get("ran") else "not needed" if receipt.get("ok") else "none"))}


def write_bank_index(out_dir: str, rows: List[Dict[str, Any]], notes: List[str], cap: float,
                     spent: float = None) -> str:
    """`spent` is the round total when it is more than the rows add up to (an
    attempt that was run again still cost money)."""
    spent = max(sum(r["cost"] for r in rows), spent or 0.0)
    lines = ["# Slipstream draft bank, 2026-11-19", "",
             "Researched drafts produced by the dry-run path (research, write, gate with one revision, "
             "targeted repair). **Nothing here is published.** No images were generated, no pull request "
             "was opened on any brand site, and no topic was checked off in any queue.", "",
             f"- Drafts banked: {sum(1 for r in rows if r['path'])} of {len(rows)} runs. "
             f"Passed the gate outright: {sum(1 for r in rows if r['verdict'] == 'PASS')}.",
             f"- Measured spend: ${spent:.2f} of the ${cap:.2f} cap (price table as cached 2026-11-03, "
             "not an invoice).",
             "- A draft marked 'needs editor' was HELD by the fact-check gate. Its .md file ends with a "
             "NEEDS EDITOR section listing each failing sentence, the rule and the quote it cites.", "",
             "| Brand | Topic | Verdict | Claims supported | Failing sentences | Sources | Repair | Cost | File |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['brand']} | {r['topic']} | {r['verdict']} | {r['claims']} | "
                     f"{r['failing_sentences']} | {r['sources']} | {r['repair']} | ${r['cost']:.2f} | "
                     f"{r['path'] or '(none)'} |")
    if notes:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in notes]
    path = os.path.join(out_dir, "INDEX.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def bank_drafts(plan_path: str) -> int:
    """Run the plan's (brand, topic) pairs in order, saving each draft and
    rewriting INDEX.md after every run. Stops cleanly before a run the cap
    cannot cover."""
    from services import slipstream_engine as se
    from services import slipstream_generate as gen
    with open(plan_path, "r", encoding="utf-8") as fh:
        plan = json.load(fh)
    out_dir, cap = plan["out"], float(plan.get("cap", 20.0))
    notes = list(plan.get("notes") or [])
    os.makedirs(out_dir, exist_ok=True)
    gen.USAGE_LOG.clear()
    budget = Budget(cap, float(plan.get("spent", 0.0)))
    # Resume: drafts already saved in this folder stay in the index. A saved
    # run for a (brand, topic) this plan runs again is replaced by the new one.
    rerun = {(j["brand"], j["topic"]) for j in plan["runs"]}
    rows: List[Dict[str, Any]] = []
    old_files = []
    for brand_dir in sorted(os.listdir(out_dir)):
        full = os.path.join(out_dir, brand_dir)
        if os.path.isdir(full):
            old_files += [os.path.join(full, f) for f in os.listdir(full)
                          if f.endswith(".json") and not f.endswith("-attempt1.json")]
    for path in sorted(old_files, key=os.path.getmtime):
        with open(path, "r", encoding="utf-8") as fh:
            old = json.load(fh)
        if (old.get("brand"), old.get("topic")) in rerun:
            os.replace(path, path[:-5] + "-attempt1.json")
            notes.append(f"{old.get('brand')}: {old.get('topic')}: an earlier attempt in this round did not "
                         f"produce a draft ({(old.get('receipt') or {}).get('error')}); it cost "
                         f"${(old.get('cost') or {}).get('total_usd', 0):.2f} and was run again.")
            continue
        rows.append(_bank_row(old, path[:-5] + ".md"))
    for job in plan["runs"]:
        brand, topic = job["brand"], job["topic"]
        if budget.total() + _RUN_RESERVE_USD > cap:
            notes.append(f"NOT RUN (spend cap): {brand}: {topic}. ${budget.total():.2f} spent; a run "
                         f"needs up to ${_RUN_RESERVE_USD:.2f} of headroom.")
            continue
        overrides = {k: job[k] for k in ("blocked_domains", "avoid_sources") if job.get(k)}
        try:
            run = run_dry(brand, topic, overrides or None, budget)
        except Exception as e:
            notes.append(f"FAILED to run {brand}: {topic}: {type(e).__name__}: {e}")
            write_bank_index(out_dir, rows, notes, cap)
            continue
        cfg = run.pop("cfg")
        receipt = run["receipt"]
        post = run["posts"][-1]["post"] if run["posts"] else None
        slug = (post or {}).get("slug") or re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:70]
        bdir = os.path.join(out_dir, brand)
        os.makedirs(bdir, exist_ok=True)
        with open(os.path.join(bdir, f"{slug}.json"), "w", encoding="utf-8") as fh:
            json.dump(run, fh, indent=2, default=str)
        path_md = os.path.join(bdir, f"{slug}.md")
        if post:
            fcr = receipt.get("factcheck") or {}
            md = readable_markdown(post, cfg, fcr.get("repair") or {})
            if not receipt.get("ok"):
                md = md.replace("NOT PUBLISHED. This is a dry-run result that passed the fact-check gate.",
                                "NOT PUBLISHED. This draft was HELD by the fact-check gate. See NEEDS EDITOR "
                                "at the end.")
                md += se.needs_editor_section(post, receipt.get("violations") or [], fcr, run.get("pack"), cfg)
            with open(path_md, "w", encoding="utf-8") as fh:
                fh.write(md)
        row = _bank_row(run, path_md)
        rows.append(row)
        if overrides:
            notes.append(f"{brand}: {topic}: run-time research preference applied: "
                         f"{len(overrides.get('blocked_domains') or [])} competitor domains blocked for this "
                         f"run only" + (f"; search told to avoid: {overrides['avoid_sources']}"
                                        if overrides.get("avoid_sources") else ""))
        write_bank_index(out_dir, rows, notes, cap, budget.total())
        print(f"{row['verdict']:<36} ${row['cost']:.2f}  total ${budget.total():.2f}  {brand}: {topic}", flush=True)
    index = write_bank_index(out_dir, rows, list(dict.fromkeys(notes)), cap, budget.total())
    print(f"INDEX   {index}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("post", nargs="?", help="path to a PR diff, an .mdx post, or a generator .json")
    ap.add_argument("--brand", help="brand key in config/slipstream_brands.yaml")
    ap.add_argument("--from-receipt", default="", metavar="RECEIPT",
                    help="repair a post an earlier --write run saved (pack re-check, repair, final gate; paid)")
    ap.add_argument("--bank", default="", metavar="PLAN",
                    help="produce the researched drafts listed in PLAN.json and index them (paid; nothing is published)")
    ap.add_argument("--cap", type=float, default=1.0, help="with --from-receipt: spend cap in USD for the round")
    ap.add_argument("--spent", type=float, default=0.0,
                    help="with --from-receipt: USD already spent this round by earlier runs")
    ap.add_argument("--llm", action="store_true", help="also run the paid verifier + editor passes")
    ap.add_argument("--json", action="store_true", help="print the raw gate result as JSON")
    ap.add_argument("--write", action="store_true",
                    help="end-to-end dry run: research, write and gate one topic (paid; nothing is published)")
    ap.add_argument("--topic", default="", help="with --write: the topic (default: next in the brand's queue)")
    ap.add_argument("--out", default="", help="with --write: directory for the saved pack, post and receipt")
    args = ap.parse_args(argv)

    if args.bank:
        try:
            return bank_drafts(args.bank)
        except Exception as e:
            print(f"bank run failed: {type(e).__name__}: {e}", file=sys.stderr)
            return 2
    if args.from_receipt:
        try:
            return repair_from_receipt(args.from_receipt, args.out, args.cap, args.spent)
        except Exception as e:
            print(f"repair from receipt failed: {type(e).__name__}: {e}", file=sys.stderr)
            return 2
    if not args.brand:
        ap.error("--brand is required")
    if args.write:
        try:
            return dry_run_write(args.brand, args.topic, args.out)
        except Exception as e:
            print(f"dry run failed: {type(e).__name__}: {e}", file=sys.stderr)
            return 2
    if not args.post:
        ap.error("a POST file is required unless --write is given")

    from services.slipstream_engine import _brand_cfg
    from services.slipstream_factcheck import run_gate

    try:
        post = load_post(args.post)
        cfg = _brand_cfg(args.brand)
    except Exception as e:
        print(f"could not read input: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    result = run_gate(post, cfg, use_llm=args.llm)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["ok"] else 1

    mode = "deterministic + verifier + editor" if args.llm else "deterministic only (no model calls)"
    print(f"POST    {post.get('title') or args.post}")
    print(f"BRAND   {args.brand}  ({cfg.get('domain')})")
    print(f"MODE    {mode}" + (f"  model={result['model']}" if args.llm else ""))
    print(f"VERDICT {'PASS' if result['ok'] else 'HOLD'}"
          + ("  (deterministic checks only; the model passes were not run)" if not args.llm else ""))
    if result.get("error"):
        print(f"GATE ERROR {result['error']}")
    print("\nLINKS")
    for link in result["links"]:
        kind = "internal" if link["internal"] else "outbound"
        print(f"  {link['state']:<9} {str(link['status'] or '-'):<4} {kind:<8} {link['href']}")
    print("\nCOUNTS  " + ", ".join(f"{k}={v}" for k, v in result["counts"].items()))
    print(f"\nVIOLATIONS ({len(result['violations'])})")
    for i, v in enumerate(result["violations"], 1):
        print(f"  {i}. {v}")
    if result["claims"]:
        print(f"\nCLAIMS ({len(result['claims'])})")
        for c in result["claims"]:
            print(f"  [{c['verdict']}] {c['claim']}  <- {c['source'] or 'no source'}")
    if result["editor"]:
        print("\nEDITOR RUBRIC")
        for e in result["editor"]:
            print(f"  {'pass' if e['pass'] else 'FAIL'}  {e['id']}  {e['evidence'][:120]}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
