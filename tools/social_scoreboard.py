"""tools/social_scoreboard.py — file 121 Phase 3: THE SCOREBOARD.

One weekly per-post table for the CMO brief: what fired, where, and what it did.
Joins the attribution registry (written by tools/social_load.py) against:

  * Zernio post analytics (impressions / engagement / clicks) per post_id
  * GA4 sessions by utm_campaign — OPTIONAL: pass --ga4-csv with columns
    utm_campaign,sessions[,conversions]. The Growth & Analytics seat owns the
    GA4 pull (avo-analytics service account); this tool owns the join, so the
    scoreboard works the day the CSV shows up and degrades gracefully until.
  * Buffer (P&P) rows carry rail="buffer"; Buffer's API exposes no per-post
    analytics on our plan, so those rows report status only.

Usage:
  doppler run -p paperclip -c prd -- python3 tools/social_scoreboard.py \
      --since 2026-07-14 [--until 2026-07-20] [--ga4-csv ga4.csv] \
      [--out ~/avo-telemetry/social_scoreboard_latest.md]

DRY facts: registry path resolution lives in tools.social_load.registry_path().
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.social_load import registry_path  # noqa: E402


def load_registry(since: str, until: Optional[str]) -> List[dict]:
    rows: List[dict] = []
    path = registry_path()
    if not os.path.exists(path):
        return rows
    for line in open(path, encoding="utf-8"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        day = str(r.get("scheduled_for") or "")[:10]
        if not day or day < since or (until and day > until):
            continue
        rows.append(r)
    # one row per post_id (re-times append nothing, but backfills can repeat)
    seen: Dict[str, dict] = {}
    for r in rows:
        pid = r.get("post_id") or f"norail-{len(seen)}"
        seen[pid] = r          # last write wins: latest row is the current truth
    return list(seen.values())


def load_ga4_csv(path: Optional[str]) -> Dict[str, dict]:
    if not path:
        return {}
    out: Dict[str, dict] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row.get("utm_campaign") or "").strip()
            if key:
                out[key] = {"sessions": row.get("sessions"),
                            "conversions": row.get("conversions")}
    return out


def zernio_metrics(post_id: str, fetch) -> Dict[str, Any]:
    try:
        data = fetch(post_id=post_id) or {}
    except Exception as e:                     # analytics must never sink the report
        return {"error": str(e)[:80]}
    # tolerate shape drift: look for the usual names at top level or one level down
    flat = dict(data)
    for v in list(data.values()):
        if isinstance(v, dict):
            flat.update(v)
    return {k: flat.get(k) for k in
            ("impressions", "engagement", "engagements", "clicks", "likes",
             "comments", "shares", "views") if flat.get(k) is not None} or \
           {"raw_keys": sorted(flat.keys())[:8]}


def _display_local(r: dict) -> str:
    """Registry rows written by the Pipe carry local time; backfilled rows carry
    UTC (tz='UTC'). Normalize display to America/Chicago so the column is honest."""
    when = str(r.get("scheduled_for") or "")
    if r.get("tz") == "UTC" and when:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        try:
            dt = datetime.fromisoformat(when.replace("Z", "+00:00"))
            return dt.astimezone(ZoneInfo("America/Chicago")).strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            pass
    return when[:16]


def build_table(rows: List[dict], ga4: Dict[str, dict], fetch) -> str:
    lines = ["| when (CT) | brand | platform | rail | content | impressions | engage | clicks | GA4 sessions |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=_display_local):
        m = zernio_metrics(r["post_id"], fetch) if r.get("rail") == "zernio" and r.get("post_id") else {}
        g = ga4.get(r.get("utm_campaign") or "", {})
        eng = m.get("engagement", m.get("engagements", ""))
        lines.append(
            f"| {_display_local(r)} | {r.get('brand')} | {r.get('platform')} "
            f"| {r.get('rail')} | {r.get('content_id')} | {m.get('impressions', '')} "
            f"| {eng} | {m.get('clicks', '')} | {g.get('sessions', '')} |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="file-121 Phase 3: weekly per-post scoreboard")
    ap.add_argument("--since", required=True, help="YYYY-MM-DD inclusive")
    ap.add_argument("--until", default=None, help="YYYY-MM-DD inclusive")
    ap.add_argument("--ga4-csv", default=None, help="csv: utm_campaign,sessions[,conversions]")
    ap.add_argument("--out", default=None, help="write markdown here as well as stdout")
    args = ap.parse_args()

    rows = load_registry(args.since, args.until)
    if not rows:
        print(f"no registry rows in window {args.since}..{args.until or 'now'}")
        return 0
    from tools.zernio import get_zernio_analytics
    table = build_table(rows, load_ga4_csv(args.ga4_csv), get_zernio_analytics)
    header = (f"# Social scoreboard — {args.since} to {args.until or 'now'}\n\n"
              f"{len(rows)} posts. GA4 join: "
              f"{'ON' if args.ga4_csv else 'OFF (pass --ga4-csv when G&A wires the pull)'}\n\n")
    doc = header + table + "\n"
    print(doc)
    if args.out:
        out = os.path.expanduser(args.out)
        open(out, "w", encoding="utf-8").write(doc)
        print(f"[written] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
