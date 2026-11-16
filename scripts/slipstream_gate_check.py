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


def dry_run_write(brand: str, topic: str = "", out_dir: str = "") -> int:
    """research -> write -> gate through the real run_brand, with every
    publishing side effect replaced. Returns the exit code."""
    from unittest import mock
    from services import slipstream_engine as se
    from services import slipstream_generate as gen

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

    gen.USAGE_LOG.clear()
    with mock.patch.object(se, "build_source_pack", _pack), \
         mock.patch.object(se, "generate_post", _generate), \
         mock.patch.object(se, "revise_post", _revise), \
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

    cost = cost_report(gen.USAGE_LOG)
    passed = bool(receipt.get("ok"))
    research = receipt.get("research") or {}
    print(f"BRAND   {brand}")
    print(f"TOPIC   {topic}")
    print(f"VERDICT {'PASS' if passed else 'HOLD'}"
          + (f"  reason={receipt.get('reason')}" if receipt.get("reason") else "")
          + (f"  error={receipt.get('error')}" if receipt.get("error") else ""))
    print(f"\nRESEARCH mode={research.get('mode')} searches={research.get('searches')} "
          f"candidates={research.get('candidates')} quotes_discarded={research.get('quotes_discarded')}")
    for s in research.get("sources") or []:
        print(f"  SOURCE  {'primary ' if s.get('primary') else ''}{s['url']}  "
              f"[{s.get('publisher')}] quotes={s.get('quotes')} figures={s.get('figures')}")
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
            json.dump({"brand": brand, "topic": topic, "receipt": receipt, "pack": seen.get("pack"),
                       "posts": seen["posts"], "pr_body": seen.get("pr_body"),
                       "violations_sent_to_writer": seen.get("violations_sent_to_writer"),
                       "cost": cost, "usage": list(gen.USAGE_LOG)}, fh, indent=2, default=str)
        print(f"\nSAVED   {os.path.join(out_dir, brand + '.json')}")
    return 0 if passed else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("post", nargs="?", help="path to a PR diff, an .mdx post, or a generator .json")
    ap.add_argument("--brand", required=True, help="brand key in config/slipstream_brands.yaml")
    ap.add_argument("--llm", action="store_true", help="also run the paid verifier + editor passes")
    ap.add_argument("--json", action="store_true", help="print the raw gate result as JSON")
    ap.add_argument("--write", action="store_true",
                    help="end-to-end dry run: research, write and gate one topic (paid; nothing is published)")
    ap.add_argument("--topic", default="", help="with --write: the topic (default: next in the brand's queue)")
    ap.add_argument("--out", default="", help="with --write: directory for the saved pack, post and receipt")
    args = ap.parse_args(argv)

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
