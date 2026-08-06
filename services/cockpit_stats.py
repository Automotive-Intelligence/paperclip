"""services/cockpit_stats.py -- read-only business-stats assembler for GET /api/cockpit.

This backs the AVO business cockpit (a business-numbers surface, separate from the
agent-fleet Pit Wall). Three design rules, in priority order:

  1. FAST on the hot path. The money numbers come from the LAST STORED TP-daily
     heartbeat, NOT a live Instantly pull. run_tp_daily() (07:15 CT cron) already
     paid the cost of paginating leads across four brands and committed the honest
     result -- "interested humans = N", the per-campaign table, the needs-review
     count -- into avo-telemetry/team_principal_state.md. We read THAT committed
     block. There is no DB cache of the receipt; the committed markdown IS the store,
     so the one GitHub Contents GET here is the stored-read (a single fast call with
     a short timeout), never the slow Instantly path.

  2. REAL data only. Every field is computed from a real source or nulled. Nothing
     is fabricated. MRR fields are explicit 0 placeholders with a Phase-2 note
     because the recurring-revenue field is not built yet.

  3. FAIL-SAFE. Each source is read independently and defensively: an unreachable
     source nulls its own fields and appends a string to health.notes. The builder
     never raises and the endpoint never 500s on a data-source outage.

Sources
-------
  money.interested_humans / needs_review / the outbound[] table
      -> latest committed TP-daily block in team_principal_state.md (parsed).
  money.pipeline_open_amount / pipeline_open_count + pipeline.*
      -> open Twenty opportunities, aggregated across the per-brand workspaces the
         codebase already knows (twenty_opportunity_log.BRAND_TO_BUSINESS_KEY),
         reusing that connector's _list_opportunities fetch + _parse_twenty_ts and
         the {amountMicros}/1e6 convention. Short timeout, degrades to null + note.
  health.watchdog
      -> services.watchdog.current_state_json() (cheap DB snapshot) mapped to
         green | warn | crit | unknown.
"""
from __future__ import annotations

import base64
import logging
import os
import re
from datetime import datetime, timezone
from statistics import median
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TP-daily heartbeat: the STORED money numbers (no live Instantly pull)
# ---------------------------------------------------------------------------

_TELEMETRY_REPO = "salesdroid/avo-telemetry"
_TP_STATE_PATH = "team_principal_state.md"

# The block header build_block() writes: "## 🏁 TP daily -- 2026-08-05" (the laptop
# used two hyphens; the em-dash form appears too). Same tolerance the watchdog uses.
_TP_HEADER_RE = re.compile(r"##\s*🏁\s*TP daily\s*[-—]+\s*(\d{4}-\d{2}-\d{2})")
_INTERESTED_RE = re.compile(r"interested humans\s*=\s*(\d+)")
_NEEDS_EYES_RE = re.compile(r"Needs eyes:\s*(\d+)\s+unclassified")
# One per-campaign data row: | brand | campaign | leads | sent | replies | **INT** |
# Cells are confined to a single line ([^|\n]+ and horizontal-only [ \t]* spacing)
# so a match can never straddle the newline between the |---|---| separator row and
# the first data row. The bolded final column + numeric leads/sent/replies then
# exclude the header and separator rows automatically.
_ROW_RE = re.compile(
    r"^\|[ \t]*([^|\n]+?)[ \t]*\|[ \t]*([^|\n]+?)[ \t]*\|"
    r"[ \t]*(\d+)[ \t]*\|[ \t]*(\d+)[ \t]*\|[ \t]*(\d+)[ \t]*\|"
    r"[ \t]*\*\*(\d+)\*\*[ \t]*\|",
    re.M,
)


def _gh_token() -> str:
    return (
        os.environ.get("SLIPSTREAM_GH_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GH_TOKEN")
        or ""
    ).strip()


def _read_state_file(path: str, timeout: int = 8) -> str:
    """Read a file from avo-telemetry via the GitHub Contents API (the same rail
    services/watchdog.py uses). Returns the decoded text, or "" if the file is
    absent (404). Raises on other transport/HTTP errors so the caller can note it.
    Seam: patched in tests so no test hits the wire."""
    headers = {"Accept": "application/vnd.github+json"}
    token = _gh_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.get(
        f"https://api.github.com/repos/{_TELEMETRY_REPO}/contents/{path}",
        headers=headers,
        timeout=timeout,
    )
    if r.status_code == 404:
        return ""
    r.raise_for_status()
    return base64.b64decode(r.json().get("content", "")).decode("utf-8")


def _latest_tp_block(content: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (date, block_text) for the newest TP-daily block, or (None, None).
    Blocks are written newest-on-top but we pick by max date to be robust."""
    matches = list(_TP_HEADER_RE.finditer(content))
    if not matches:
        return None, None
    best = max(matches, key=lambda m: m.group(1))
    start = best.start()
    nxt = re.search(r"\n##\s", content[best.end():])
    end = best.end() + nxt.start() if nxt else len(content)
    return best.group(1), content[start:end]


def read_latest_tp_daily(reader: Optional[Callable[[str], str]] = None) -> Dict[str, Any]:
    """Parse the latest committed TP-daily heartbeat into the money + outbound
    inputs. Fail-safe: any read/parse problem returns interested/needs_review=None
    with a `note`, never raises.

    Returns: {ok, date, interested, needs_review, outbound[], note}
    """
    reader = reader or _read_state_file
    out: Dict[str, Any] = {
        "ok": False,
        "date": None,
        "interested": None,
        "needs_review": None,
        "outbound": [],
        "note": None,
    }
    try:
        content = reader(_TP_STATE_PATH)
    except Exception as e:  # transport / auth / decode
        out["note"] = f"TP-daily heartbeat unreachable ({type(e).__name__}); money numbers unknown"
        return out
    if not content:
        out["note"] = "TP-daily state file empty or not found; money numbers unknown"
        return out

    date, block = _latest_tp_block(content)
    if not block:
        out["note"] = "no TP-daily block found in team_principal_state.md"
        return out
    out["date"] = date

    im = _INTERESTED_RE.search(block)
    if not im:
        out["note"] = f"TP-daily block for {date} present but 'interested humans' line unparseable"
        return out
    out["interested"] = int(im.group(1))

    nm = _NEEDS_EYES_RE.search(block)
    # No "Needs eyes" line means zero unclassified replies were surfaced that day.
    out["needs_review"] = int(nm.group(1)) if nm else 0

    rows: List[Dict[str, Any]] = []
    for m in _ROW_RE.finditer(block):
        brand, campaign, leads, sent, replies, interested = m.groups()
        rows.append({
            "brand": brand.strip(),
            "campaign": campaign.strip(),
            "leads": int(leads),
            "sent": int(sent),
            "replies": int(replies),
            "interested": int(interested),
            # needs_review is a global de-duped count in the heartbeat, not split
            # per campaign, so per-row it is 0; the money.needs_review is the total.
            "needs_review": 0,
        })
    out["outbound"] = rows
    out["ok"] = True
    return out


# ---------------------------------------------------------------------------
# Twenty pipeline: open opportunities across the per-brand workspaces
# ---------------------------------------------------------------------------

# Terminal stages excluded from OPEN pipeline. twenty_opportunity_log excludes
# {won,closed_won,lost,closed_lost}; these workspaces use the stock Twenty stage
# set where CUSTOMER is the terminal "won" stage (tools/twenty._WON_STAGE), so we
# add it here to avoid counting closed-won deals as open pipeline.
_TERMINAL_STAGES = {"won", "closed_won", "lost", "closed_lost", "customer"}


def read_pipeline() -> Dict[str, Any]:
    """Aggregate open Twenty opportunities across every configured brand workspace.
    One _list_opportunities fetch per workspace (reused from the CRO connector),
    then open-amount / open-count / new-per-week / velocity computed locally.

    Fail-safe per workspace: a workspace that is unconfigured or errors is skipped
    with a note; if NONE are readable the pipeline fields come back null.

    Returns: {pipeline_open_amount, pipeline_open_count, new_opps_per_week,
              coverage_ratio, velocity_days, notes[], wired_brands[]}
    """
    result: Dict[str, Any] = {
        "pipeline_open_amount": None,
        "pipeline_open_count": None,
        "new_opps_per_week": None,
        "coverage_ratio": None,
        "velocity_days": None,
        "notes": [],
        "wired_brands": [],
    }

    try:
        from services.metric_connectors import twenty_opportunity_log as tol
        from tools.twenty import twenty_ready
    except Exception as e:  # import-time failure -> whole pipeline unknown
        result["notes"].append(f"Twenty pipeline module unavailable ({type(e).__name__})")
        return result

    brand_map = {b: k for b, k in tol.BRAND_TO_BUSINESS_KEY.items() if k}
    now_ts = datetime.now(timezone.utc).timestamp()
    week_cutoff = now_ts - 7 * 86400
    velocity_cutoff = now_ts - 90 * 86400

    open_amount = 0.0
    open_count = 0
    new_per_week = 0
    spans_days: List[float] = []
    any_wired = False

    for brand, biz_key in brand_map.items():
        if not twenty_ready(biz_key):
            result["notes"].append(f"Twenty workspace for {brand} not configured (skipped)")
            continue
        try:
            opps = tol._list_opportunities(biz_key)
        except Exception as e:
            result["notes"].append(f"Twenty read failed for {brand} ({type(e).__name__}); excluded from pipeline")
            continue

        any_wired = True
        result["wired_brands"].append(brand)
        for o in opps:
            stage = (o.get("stage") or "").lower()
            created_ts = tol._parse_twenty_ts(o.get("createdAt") or "")

            if stage not in _TERMINAL_STAGES:
                open_count += 1
                amount = o.get("amount") or {}
                if isinstance(amount, dict):
                    micros = amount.get("amountMicros") or 0
                    try:
                        open_amount += float(micros) / 1_000_000.0
                    except (TypeError, ValueError):
                        pass

            if created_ts is not None and created_ts >= week_cutoff:
                new_per_week += 1

            if stage in {"won", "closed_won", "customer"}:
                close_ts = tol._parse_twenty_ts(o.get("closeDate") or "")
                if close_ts is not None and created_ts is not None and close_ts >= velocity_cutoff:
                    spans_days.append((close_ts - created_ts) / 86400.0)

    if not any_wired:
        result["notes"].append("no Twenty workspace reachable; pipeline numbers unknown")
        return result

    result["pipeline_open_amount"] = round(open_amount, 2)
    result["pipeline_open_count"] = open_count
    result["new_opps_per_week"] = float(new_per_week)
    result["velocity_days"] = round(median(spans_days), 1) if spans_days else None
    # coverage_ratio = open pipeline / remaining quarterly target. Targets are not
    # configured yet (quarterly_target_per_brand unset in the CRO scorecard), so we
    # leave the ratio null rather than invent a denominator.
    result["notes"].append("coverage_ratio pending quarterly targets (not configured)")
    return result


# ---------------------------------------------------------------------------
# Watchdog health: green | warn | crit | unknown
# ---------------------------------------------------------------------------

def read_watchdog_health() -> Tuple[str, Optional[str], List[str]]:
    """Map the watchdog DB snapshot to a traffic light. Returns
    (status, swept_at_iso, notes). Never raises."""
    notes: List[str] = []
    try:
        from services.watchdog import current_state_json
        state = current_state_json()
    except Exception as e:
        return "unknown", None, [f"watchdog snapshot unavailable ({type(e).__name__})"]

    if not state.get("ok"):
        return "unknown", None, [f"watchdog store error: {state.get('error')}"]

    active = state.get("active_anomalies") or []
    severities = {(a.get("severity") or "").lower() for a in active}
    if "critical" in severities:
        status = "crit"
    elif active:
        status = "warn"
    else:
        status = "green"

    if active:
        notes.append(f"{len(active)} active watchdog anomaly(ies)")
    return status, state.get("swept_at"), notes


# ---------------------------------------------------------------------------
# Assembler
# ---------------------------------------------------------------------------

def build_cockpit() -> Dict[str, Any]:
    """Assemble the full /api/cockpit contract. Reads every source defensively;
    never raises. This dict is what the endpoint returns verbatim."""
    generated_at = datetime.now(timezone.utc).isoformat()
    notes: List[str] = []

    tp = read_latest_tp_daily()
    if tp.get("note"):
        notes.append(tp["note"])

    pipe = read_pipeline()
    notes.extend(pipe.get("notes") or [])

    wd_status, _swept_at, wd_notes = read_watchdog_health()
    notes.extend(wd_notes)

    money = {
        "interested_humans": tp.get("interested"),
        "needs_review": tp.get("needs_review"),
        "pipeline_open_amount": pipe.get("pipeline_open_amount"),
        "pipeline_open_count": pipe.get("pipeline_open_count"),
        "mrr_committed": 0,
        "mrr_pipeline_weighted": 0,
        "mrr_note": "MRR forecast pending recurring field (Phase 2)",
    }

    return {
        "generated_at": generated_at,
        "money": money,
        "outbound": tp.get("outbound") or [],
        "pipeline": {
            "new_opps_per_week": pipe.get("new_opps_per_week"),
            "coverage_ratio": pipe.get("coverage_ratio"),
            "velocity_days": pipe.get("velocity_days"),
        },
        "health": {
            "watchdog": wd_status,
            "last_daily": tp.get("date"),
            "notes": notes,
        },
        "generated_by": "api/cockpit",
    }


__all__ = [
    "build_cockpit",
    "read_latest_tp_daily",
    "read_pipeline",
    "read_watchdog_health",
]
