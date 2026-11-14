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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("post", help="path to a PR diff, an .mdx post, or a generator .json")
    ap.add_argument("--brand", required=True, help="brand key in config/slipstream_brands.yaml")
    ap.add_argument("--llm", action="store_true", help="also run the paid verifier + editor passes")
    ap.add_argument("--json", action="store_true", help="print the raw gate result as JSON")
    args = ap.parse_args(argv)

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
