#!/usr/bin/env python3
"""Calibrate the Slipstream AI-detection soft signal.

Scores two folders of plain-text pieces with the SAME code path the engine uses
(services.slipstream_ai_detect.score_chunks + verdict), then picks the flag
threshold: the LOWEST score line at which at most 1 in 20 known-human pieces
flags. Nothing here touches the live pipeline.

  human/  one .txt per piece known to be written by a person
  ai/     one .txt per RAW engine draft

Usage (needs torch + transformers, see requirements-ai-detect.txt):

  python3 scripts/slipstream_ai_detect_calibrate.py build   --out DIR \
      --human-export Squarespace-Wordpress-Export.xml \
      --ai-repo ~/automotive-intelligence-site --ai-repo ~/ai-phone-guy-site
  python3 scripts/slipstream_ai_detect_calibrate.py score   --dir DIR
  python3 scripts/slipstream_ai_detect_calibrate.py report  --dir DIR

`build` is specific to the first calibration set; any folder of .txt files in
human/ and ai/ works for `score` and `report`.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import slipstream_ai_detect as ad  # noqa: E402

MAX_HUMAN_FLAG_RATE = 1 / 20
# ChatGPT's public release. A piece published before it is treated as human.
PRE_LLM_CUTOFF = "2022-11-30"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:70]


# ---- build ------------------------------------------------------------------

def html_to_prose(h: str) -> str:
    h = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?i)</(p|h[1-6]|li|div|blockquote)>|<br\s*/?>", "\n", h)
    h = html.unescape(re.sub(r"<[^>]+>", " ", h))
    lines = [" ".join(ln.split()) for ln in h.splitlines()]
    return " ".join(ln for ln in lines if ln)


def build_human(export_xml: str, out_dir: str) -> list:
    x = open(export_xml, encoding="utf-8", errors="ignore").read()
    rows, seen = [], set()
    for it in re.findall(r"<item>(.*?)</item>", x, re.S):
        g = lambda tag: (re.search(rf"<{tag}>(.*?)</{tag}>", it, re.S) or [None, ""])[1]
        if g("wp:post_type") != "post":
            continue
        date = g("wp:post_date")[:10]
        if not date or date > PRE_LLM_CUTOFF:
            continue                                  # after ChatGPT: provenance unknown
        m = re.search(r"<content:encoded><!\[CDATA\[(.*?)\]\]>", it, re.S)
        text = html_to_prose(m.group(1)) if m else ""
        title = html.unescape(g("title"))
        if ad.word_count(text) < 250 or title in seen:
            continue                                  # too short, or a duplicate post
        seen.add(title)
        name = f"{date}-{_slug(title)}.txt"
        open(os.path.join(out_dir, name), "w", encoding="utf-8").write(text)
        rows.append({"file": name, "title": title, "date": date, "source": "calling.digital blog export"})
    return rows


def build_ai(repo: str, out_dir: str) -> list:
    """Every blog post the engine ADDED (commit subject 'content: ...'), read at
    the commit that added it, so it is the raw draft and not a later edit."""
    repo = os.path.expanduser(repo)
    git = lambda *a: subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True, check=True).stdout
    log = git("log", "origin/main", "--diff-filter=A", "--format=@%H %ad", "--date=short",
              "--name-only", "--grep=^content: ", "--", "src/content/blog/*.mdx")
    rows, commit, date = [], None, None
    for ln in log.splitlines():
        if ln.startswith("@"):
            commit, date = ln[1:].split()
        elif ln.endswith(".mdx"):
            raw = git("show", f"{commit}:{ln}")
            body = re.sub(r"\A---\n.*?\n---\n", "", raw, flags=re.S)
            title = (re.search(r'^title:\s*"?(.*?)"?\s*$', raw, re.M) or [None, ln])[1]
            text = ad.post_text({"body_mdx": body})   # the exact text the engine step scores
            if ad.word_count(text) < 250:
                continue
            name = f"{date}-{os.path.basename(repo)[:12]}-{_slug(os.path.basename(ln)[:-4])}.txt"
            open(os.path.join(out_dir, name), "w", encoding="utf-8").write(text)
            rows.append({"file": name, "title": title, "date": date,
                         "source": f"{os.path.basename(repo)}@{commit[:7]}"})
    return rows


def cmd_build(a) -> None:
    manifest = {}
    for label in ("human", "ai"):
        os.makedirs(os.path.join(a.out, label), exist_ok=True)
    manifest["human"] = build_human(os.path.expanduser(a.human_export), os.path.join(a.out, "human"))
    manifest["ai"] = [r for repo in a.ai_repo for r in build_ai(repo, os.path.join(a.out, "ai"))]
    json.dump(manifest, open(os.path.join(a.out, "manifest.json"), "w"), indent=1)
    print({k: len(v) for k, v in manifest.items()})


# ---- score ------------------------------------------------------------------

def cmd_score(a) -> None:
    path = os.path.join(a.dir, "scores.json")
    scores = json.load(open(path)) if os.path.exists(path) else {}
    for label in ("human", "ai"):
        folder = os.path.join(a.dir, label)
        for name in sorted(os.listdir(folder)):
            key = f"{label}/{name}"
            if not name.endswith(".txt") or key in scores:
                continue
            text = open(os.path.join(folder, name), encoding="utf-8").read()
            t0 = time.time()
            chunk_scores, _ = ad.score_chunks(text, a.chunk_tokens, a.overlap_tokens)
            scores[key] = {"label": label, "words": ad.word_count(text),
                           "chunks": [round(s, 4) for s in chunk_scores],
                           "score": ad.verdict(chunk_scores, [""] * len(chunk_scores), None)["score"],
                           "seconds": round(time.time() - t0, 1)}
            json.dump(scores, open(path, "w"), indent=1)          # resumable
            print(f"{scores[key]['score']:.3f}  {len(chunk_scores)} chunks  "
                  f"{scores[key]['seconds']}s  {key}", flush=True)


# ---- report -----------------------------------------------------------------

def pick_threshold(human_scores, max_rate: float = MAX_HUMAN_FLAG_RATE) -> float:
    """Lowest 2-decimal line where flagged humans (score >= line) stay at or
    under max_rate. Lowest, because every step lower catches more AI drafts."""
    allowed = math.floor(len(human_scores) * max_rate)
    for i in range(0, 101):
        thr = i / 100
        if sum(1 for s in human_scores if s >= thr) <= allowed:
            return thr
    return 1.01                                        # nothing can flag


def rate(scores, thr: float):
    n = sum(1 for s in scores if s >= thr)
    return n, len(scores), (n / len(scores) if scores else 0.0)


def cmd_report(a) -> None:
    scores = json.load(open(os.path.join(a.dir, "scores.json")))
    H = sorted((v["score"], k) for k, v in scores.items() if v["label"] == "human")
    A = sorted((v["score"], k) for k, v in scores.items() if v["label"] == "ai")
    hs, as_ = [s for s, _ in H], [s for s, _ in A]
    thr = pick_threshold(hs)
    out = {"threshold": thr, "human": rate(hs, thr), "ai": rate(as_, thr),
           "human_n": len(hs), "ai_n": len(as_)}
    # Separation: the chance a random AI draft scores HIGHER than a random human
    # piece. 1.0 is perfect, 0.5 is a coin flip, under 0.5 means it reads them
    # backwards. Python decides: the check is only usable when the threshold both
    # holds the human flag rate AND still catches at least half the AI drafts.
    out["separation"] = round(sum((x > y) + 0.5 * (x == y) for x in as_ for y in hs)
                              / max(1, len(as_) * len(hs)), 4)
    out["usable"] = bool(out["human"][2] <= MAX_HUMAN_FLAG_RATE and out["ai"][2] >= 0.5)
    out["verdict"] = ("USABLE as a soft signal at this threshold" if out["usable"] else
                      "NOT USABLE: no threshold keeps human flags at or under 1 in 20 while "
                      "catching at least half the AI drafts. Leave ai_detect.enabled false.")
    # Honest check: pick the line on every other human piece, test it on the rest.
    odd, even = hs[1::2], hs[0::2]
    t_odd, t_even = pick_threshold(odd), pick_threshold(even)
    out["holdout"] = {"threshold_from_half_a": t_odd, "half_b_flagged": rate(even, t_odd),
                      "threshold_from_half_b": t_even, "half_a_flagged": rate(odd, t_even)}
    q = lambda xs, p: xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None
    for name, xs in (("human", hs), ("ai", as_)):
        out[f"{name}_dist"] = {"min": xs[0], "p25": q(xs, .25), "median": q(xs, .5),
                               "p75": q(xs, .75), "p95": q(xs, .95), "max": xs[-1]} if xs else {}
    out["sweep"] = [{"threshold": t, "human_flagged": rate(hs, t)[0], "ai_flagged": rate(as_, t)[0]}
                    for t in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99)]
    out["human_flagged_files"] = [k for s, k in H if s >= thr]
    out["ai_missed_files"] = [(k, s) for s, k in A if s < thr]
    secs = [v["seconds"] for v in scores.values()]
    out["seconds_per_piece"] = {"median": sorted(secs)[len(secs) // 2], "max": max(secs)}
    json.dump(out, open(os.path.join(a.dir, "report.json"), "w"), indent=1)
    print(json.dumps(out, indent=1))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--out", required=True)
    b.add_argument("--human-export", required=True); b.add_argument("--ai-repo", action="append", default=[])
    b.set_defaults(fn=cmd_build)
    s = sub.add_parser("score"); s.add_argument("--dir", required=True)
    s.add_argument("--chunk-tokens", type=int, default=ad.DEFAULTS["chunk_tokens"])
    s.add_argument("--overlap-tokens", type=int, default=ad.DEFAULTS["overlap_tokens"])
    s.set_defaults(fn=cmd_score)
    r = sub.add_parser("report"); r.add_argument("--dir", required=True); r.set_defaults(fn=cmd_report)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
