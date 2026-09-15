"""agent_output_audit.py - does each scheduled agent actually PRODUCE anything?

Born 2026-09-15 from Michael's question: "that's a lot of tasks for nothing to
be produced." The triage that day found ~690 of ~750 weekly agent runs emitting
nothing usable: agents failing a gate on every run, paused agents logging that
they are paused 49x/week, agents whose entire output is "X run completed at
<timestamp>", and agents emitting garbled or wrong-language text.

Activity was measured; OUTPUT never was. This is the missing meter.

Verdicts per agent, over a lookback window:
  PRODUCING   real content, no failure signature
  FAILING     every run matches a known failure signature
  IDLE        runs, but content is only a completion stamp (no work product)
  PAUSED      runs only to report that it is disabled
  GARBLED     output present but wrong-language / template-unfilled / stale-dated
  SILENT      registered somewhere but no rows in the window

Read-only. Emits a verdict table; the watchdog turns FAILING/PAUSED/IDLE into
one reconciled anomaly. Nothing here disables a job - that stays a human call.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone

MIN_REAL_OUTPUT = 200          # chars; below this is a stamp, not a work product
FAIL_SIGNATURES = (
    "OPERATING BRIEF FAILURE",
    "Briefing blocked",
    '"status": "paused"',
    "!= 1 (Sales Desk pause",
)
IDLE_PATTERN = re.compile(r"^\s*\w[\w_]* run completed at \S+\s*$")
# Output that is present but not usable: CJK/Arabic/Thai/Bengali blocks in an
# English-only org, or an unfilled template placeholder.
GARBLED_PATTERN = re.compile(r"[؀-ۿऀ-ॿ฀-๿一-鿿ঀ-৿]{3,}"
                             r"|\[Insert [^\]]+\]")
STALE_YEAR = re.compile(r"\b(2023|2024)\b")


def classify(rows: list[dict]) -> dict:
    """rows: [{content, created_at}] for ONE agent, newest first."""
    if not rows:
        return {"verdict": "SILENT", "runs": 0, "detail": "no rows in window"}
    n = len(rows)
    fails = idles = garbled = paused = real = 0
    for r in rows:
        c = (r.get("content") or "").strip()
        if any(sig in c for sig in FAIL_SIGNATURES):
            if "paused" in c.lower() or "Sales Desk pause" in c:
                paused += 1
            else:
                fails += 1
            continue
        if len(c) < MIN_REAL_OUTPUT or IDLE_PATTERN.match(c):
            idles += 1
            continue
        if GARBLED_PATTERN.search(c[:600]) or STALE_YEAR.search(c[:300]):
            garbled += 1
            continue
        real += 1
    if real == 0 and paused == n:
        v, d = "PAUSED", f"all {n} runs report disabled; the JOB should be unregistered too"
    elif real == 0 and fails > 0:
        v, d = "FAILING", f"{fails}/{n} runs hit a known failure signature, 0 produced work"
    elif real == 0 and idles == n:
        v, d = "IDLE", f"all {n} runs logged only a completion stamp (<{MIN_REAL_OUTPUT} chars)"
    elif real == 0 and garbled > 0:
        v, d = "GARBLED", f"{garbled}/{n} runs produced wrong-language, stale-dated or unfilled output"
    elif real < n / 2:
        v, d = "GARBLED", f"only {real}/{n} runs produced usable output"
    else:
        v, d = "PRODUCING", f"{real}/{n} runs produced real content"
    return {"verdict": v, "runs": n, "real": real, "detail": d}


def audit(days: int = 7) -> list[dict]:
    from services.database import fetch_all
    rows = fetch_all(
        "SELECT agent_name, content, created_at FROM agent_logs "
        f"WHERE created_at > now() - interval '{int(days)} days' ORDER BY agent_name, created_at DESC")
    by: dict[str, list] = {}
    for r in rows:
        name = r[0] if isinstance(r, tuple) else r["agent_name"]
        content = r[1] if isinstance(r, tuple) else r["content"]
        by.setdefault(name, []).append({"content": content})
    out = []
    for name, rs in sorted(by.items()):
        res = classify(rs)
        res["agent"] = name
        out.append(res)
    return out


def main() -> int:
    try:
        results = audit(7)
    except Exception as e:
        print(f"agent_output_audit: FAILED to query agent_logs: {e}", file=sys.stderr)
        return 1
    order = {"FAILING": 0, "PAUSED": 1, "IDLE": 2, "GARBLED": 3, "SILENT": 4, "PRODUCING": 5}
    results.sort(key=lambda r: (order.get(r["verdict"], 9), -r["runs"]))
    wasted = sum(r["runs"] for r in results if r["verdict"] in ("FAILING", "PAUSED", "IDLE", "GARBLED"))
    total = sum(r["runs"] for r in results)
    print(f"agent output audit, 7d - {total} runs across {len(results)} agents, "
          f"{wasted} produced nothing usable\n")
    for r in results:
        print(f"  {r['verdict']:<10} {r['agent'][:18]:<18} runs={r['runs']:<4} {r['detail']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
