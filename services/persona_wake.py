"""services/persona_wake.py — Persona Cron Loop Phase C1 runner.

Wakes a persona on cadence. Per Phase C plan (PR #94, merged):

  1. Load scorecard (services/persona_scorecards/<persona>.yaml)
  2. Load decision runbook (services/persona_runbooks/<persona>.yaml)
  3. Pull latest kpi_snapshots per (persona, kpi_name, brand)
  4. Classify each KPI green / yellow / red vs scorecard thresholds
  5. For each non-green KPI, consult runbook for candidate levers,
     let a Claude session pick (v1 uses lever[0] deterministically —
     the Claude decision layer lands in C2 with adversarial review)
  6. Never execute levers marked reversibility=RED — those queue
     as RED items for the Owner's Brief instead
  7. Write persona_wake_events audit row
  8. Append the persona's paragraph to owner_brief_queue

v1 is deterministic (no Claude call yet) — proves the classify + queue
plumbing works without spawning agent activity. C2 adds the Claude
selection + adversarial reviewer. C3 wires the Owner's Brief composer.

The wake loop NEVER raises to the scheduler — a persona wake failure
logs, writes a persona_wake_events row with error_detail, and returns.
Other personas keep running.
"""

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from services.database import execute_query, fetch_all

logger = logging.getLogger(__name__)


SCORECARDS_DIR = Path(__file__).resolve().parent / "persona_scorecards"
RUNBOOKS_DIR = Path(__file__).resolve().parent / "persona_runbooks"


# ── Status classification ───────────────────────────────────────────────────


def _classify(value_numeric: Optional[float], kpi_spec: dict, snapshot_status: str) -> str:
    """Return one of green / yellow / red / no_data.

    Rules:
      - snapshot_status != 'ok' → collapses to yellow (stale/no_data/schema_drift)
        OR red (connector_down/timeout/rate_limited). Keeps executive
        aware of measurement gaps without crying wolf.
      - value_numeric None with 'ok' status → no_data (shouldn't happen but defensive)
      - No target set → 'no_data' (Phase A target not locked yet)
      - Compare value to threshold_red / threshold_yellow honoring direction:
        for %-uptime style, higher is better; for error-rate style, lower is better.
        We infer direction from threshold order: red < yellow → lower is better,
        red > yellow → higher is better.
    """
    if snapshot_status in ("connector_down", "timeout", "rate_limited"):
        return "red"
    if snapshot_status in ("stale", "no_data", "schema_drift"):
        return "yellow"

    target = kpi_spec.get("target")
    yellow = kpi_spec.get("threshold_yellow")
    red = kpi_spec.get("threshold_red")

    if value_numeric is None:
        return "no_data"
    if target is None or yellow is None or red is None:
        # Target-not-locked → surface as no_data rather than misleading green
        return "no_data"

    try:
        v = float(value_numeric)
        y = float(yellow)
        r = float(red)
    except (TypeError, ValueError):
        return "no_data"

    # Direction: red < yellow means lower is worse (uptime %, completion %)
    #            red > yellow means higher is worse (error rate, burn rate)
    lower_is_worse = r < y
    if lower_is_worse:
        if v <= r:
            return "red"
        if v <= y:
            return "yellow"
        return "green"
    else:
        if v >= r:
            return "red"
        if v >= y:
            return "yellow"
        return "green"


# ── Data loading ────────────────────────────────────────────────────────────


def _load_scorecard(persona: str) -> Optional[dict]:
    path = SCORECARDS_DIR / f"{persona}.yaml"
    if not path.exists():
        logger.warning("[persona_wake] scorecard missing for %s: %s", persona, path)
        return None
    try:
        with open(path) as h:
            return yaml.safe_load(h) or {}
    except Exception as e:
        logger.error("[persona_wake] scorecard load failed for %s: %s", persona, e)
        return None


def _load_runbook(persona: str) -> dict:
    """Returns runbook dict or empty (empty = persona wakes but has no
    autonomous levers, only reports status). C1 ships bt.yaml; others
    land in C4/C5 — until then, they wake without execution."""
    path = RUNBOOKS_DIR / f"{persona}.yaml"
    if not path.exists():
        return {}
    try:
        with open(path) as h:
            return yaml.safe_load(h) or {}
    except Exception as e:
        logger.error("[persona_wake] runbook load failed for %s: %s", persona, e)
        return {}


def _latest_snapshots(persona: str) -> List[dict]:
    """Pull the most recent snapshot per (kpi_name, brand) for this persona
    within the last 25h (captures hourly + daily; longer-cadence KPIs
    outside window surface as stale via _classify)."""
    try:
        rows = fetch_all(
            """
            SELECT DISTINCT ON (kpi_name, COALESCE(brand, ''))
                kpi_name, brand, value_numeric, unit, status, ts_collected,
                error_detail
            FROM kpi_snapshots
            WHERE persona = %s
              AND ts_collected > NOW() - INTERVAL '25 hours'
            ORDER BY kpi_name, COALESCE(brand, ''), ts_collected DESC
            """,
            (persona,),
        )
    except Exception as e:
        logger.warning("[persona_wake] snapshot query failed for %s: %s", persona, e)
        return []
    # fetch_all returns tuples; hydrate to dicts for readable downstream code
    out = []
    for r in rows:
        out.append({
            "kpi_name": r[0],
            "brand": r[1],
            "value_numeric": float(r[2]) if r[2] is not None else None,
            "unit": r[3],
            "status": r[4],
            "ts_collected": r[5],
            "error_detail": r[6],
        })
    return out


# ── Decision layer (v1: deterministic; C2 adds Claude + adversarial review) ─


def _select_lever(rules_for_kpi: list, kpi_status: str) -> Optional[dict]:
    """v1: pick the first non-RED lever matching the KPI's status. C2
    replaces this with a Claude session that reads selection_prompt and
    picks with an adversarial refuter. v1 keeps the plumbing honest
    without spinning up LLM cost per wake."""
    for rule in rules_for_kpi:
        if (rule.get("when_status") or "").lower() != kpi_status:
            continue
        for lever in rule.get("candidate_levers") or []:
            reversibility = (lever.get("reversibility") or "").upper()
            if reversibility == "RED":
                # RED never auto-executes — caller queues these as escalations
                return {"__escalate__": True, "lever": lever}
            return {"__escalate__": False, "lever": lever}
    return None


def _rules_for_kpi(runbook: dict, kpi_name: str) -> list:
    """Return runbook rules that match this KPI name (both yellow and red)."""
    all_rules = runbook.get("rules") or []
    return [r for r in all_rules if (r.get("when_kpi") or "") == kpi_name]


# ── Owner's Brief contribution ──────────────────────────────────────────────


def _compose_contribution(
    persona: str, scorecard: dict, kpi_evals: list, actions_taken: list, red_items: list
) -> str:
    """One paragraph per persona for the Owner's Brief. Same shape across
    all 9 personas so the brief composer (C3) can concatenate + sort.

    Format is stable:
      {persona}: N green / M yellow / K red across P KPIs. This window: {actions}.
      Still RED for your call: {bullet list, or 'none'}.
    """
    n_green = sum(1 for e in kpi_evals if e["status"] == "green")
    n_yellow = sum(1 for e in kpi_evals if e["status"] == "yellow")
    n_red = sum(1 for e in kpi_evals if e["status"] == "red")
    total = len(kpi_evals)

    display_name = scorecard.get("display_name", persona)
    action_summary = ", ".join(a.get("id", "unknown") for a in actions_taken) if actions_taken else "no autonomous action needed"

    red_lines = ""
    if red_items:
        red_lines = "\n    RED items for your call:\n" + "\n".join(
            f"      - {r.get('summary', r.get('kpi_name', 'RED item'))}"
            for r in red_items
        )
    else:
        red_lines = "\n    RED items for your call: none."

    return (
        f"  {display_name}: {n_green} green / {n_yellow} yellow / {n_red} red across {total} KPIs.\n"
        f"    This window: {action_summary}."
        f"{red_lines}"
    )


# ── Persistence ─────────────────────────────────────────────────────────────


def _persist_wake_event(
    persona: str, cadence: str, tally: dict, actions_taken: list,
    duration_ms: int, error_detail: Optional[str] = None,
) -> None:
    try:
        execute_query(
            """
            INSERT INTO persona_wake_events
              (persona, cadence, kpis_evaluated, green_count, yellow_count,
               red_count, actions_taken, duration_ms, error_detail)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            """,
            (
                persona, cadence,
                tally.get("kpis_evaluated", 0),
                tally.get("green", 0),
                tally.get("yellow", 0),
                tally.get("red", 0),
                json.dumps(actions_taken, default=str)[:65535],
                int(duration_ms),
                error_detail,
            ),
        )
    except Exception as e:
        # Persistence failure never breaks the wake loop
        logger.error("[persona_wake] persist wake_event failed for %s: %s", persona, e)


def _brief_window_now() -> str:
    """Morning wakes (before noon CST-ish) contribute to 'morning' brief;
    afternoon+ contributes to 'evening'. Uses UTC hour; brief composer
    handles TZ display."""
    return "morning" if datetime.now(timezone.utc).hour < 17 else "evening"


def _persist_brief_contribution(
    persona: str, contribution: str, red_items: list, actions_taken: list, kpi_evals: list,
) -> None:
    """Insert (or update via ON CONFLICT — one contribution per persona per
    brief window per calendar day). Later wakes on the same window update
    the row so the brief sees the latest state."""
    window = _brief_window_now()
    # Top-3 movers by absolute value change vs 24h prior — placeholder in v1;
    # C3 computes real deltas from the previous cycle's snapshots.
    kpi_movers = [
        {"kpi": e["kpi_name"], "value": e["value_numeric"], "status": e["status"]}
        for e in kpi_evals[:3]
    ]
    shipped = [a for a in actions_taken if not a.get("escalated")]
    try:
        execute_query(
            """
            INSERT INTO owner_brief_queue
              (persona, brief_window, contribution, red_items, shipped_items, kpi_movers)
            VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)
            ON CONFLICT (persona, brief_window, ((ts_added AT TIME ZONE 'UTC')::date))
            DO UPDATE SET
                contribution = EXCLUDED.contribution,
                red_items    = EXCLUDED.red_items,
                shipped_items = EXCLUDED.shipped_items,
                kpi_movers   = EXCLUDED.kpi_movers,
                ts_added     = NOW(),
                ts_sent      = NULL
            """,
            (
                persona, window, contribution,
                json.dumps(red_items, default=str)[:65535],
                json.dumps(shipped, default=str)[:65535],
                json.dumps(kpi_movers, default=str)[:65535],
            ),
        )
    except Exception as e:
        logger.error("[persona_wake] persist brief_contribution failed for %s: %s", persona, e)


# ── Wake entry point ────────────────────────────────────────────────────────


def wake(persona: str, cadence: str) -> dict:
    """Wake a single persona. Returns summary dict for the caller/scheduler.

    Never raises — a persona-side failure logs, persists an error wake_event,
    and returns a summary with error_detail. Other personas keep running.
    """
    started_at = time.monotonic()
    scorecard = _load_scorecard(persona)
    if scorecard is None:
        return {"persona": persona, "status": "no_scorecard", "kpis_evaluated": 0}

    runbook = _load_runbook(persona)
    snapshots = _latest_snapshots(persona)
    snapshot_index = {(s["kpi_name"], s["brand"]): s for s in snapshots}

    kpi_evals: list = []
    actions_taken: list = []
    red_items: list = []
    tally = {"green": 0, "yellow": 0, "red": 0, "no_data": 0, "kpis_evaluated": 0}

    kpis = scorecard.get("kpis") or []
    max_actions = (runbook.get("global_constraints") or {}).get("max_actions_per_wake", 5)

    for kpi in kpis:
        kpi_name = kpi.get("name") or ""
        per_brand = kpi.get("per_brand") or [None]  # None = org-level KPI
        for brand in per_brand:
            snap = snapshot_index.get((kpi_name, brand))
            snap_status = (snap or {}).get("status", "no_data")
            snap_value = (snap or {}).get("value_numeric")
            status = _classify(snap_value, kpi, snap_status)
            tally[status] = tally.get(status, 0) + 1
            tally["kpis_evaluated"] += 1

            eval_row = {
                "kpi_name": kpi_name,
                "brand": brand,
                "status": status,
                "value_numeric": snap_value,
                "snap_status": snap_status,
            }
            kpi_evals.append(eval_row)

            if status not in ("yellow", "red"):
                continue
            if len(actions_taken) >= max_actions:
                # Cap hit — every additional non-green KPI queues as RED
                red_items.append({
                    "kpi_name": kpi_name,
                    "brand": brand,
                    "status": status,
                    "summary": f"{kpi_name}{'/' + brand if brand else ''}: {status} (action cap hit; queued)",
                })
                continue

            rules = _rules_for_kpi(runbook, kpi_name)
            if not rules:
                # No runbook entry for this KPI — surface as RED item so
                # the persona doesn't silently ignore a real gap
                red_items.append({
                    "kpi_name": kpi_name,
                    "brand": brand,
                    "status": status,
                    "summary": f"{kpi_name}: {status} — no runbook lever defined, needs human attention",
                })
                continue

            selection = _select_lever(rules, status)
            if not selection:
                red_items.append({
                    "kpi_name": kpi_name,
                    "brand": brand,
                    "status": status,
                    "summary": f"{kpi_name}: {status} — runbook had no matching lever",
                })
                continue

            lever = selection.get("lever") or {}
            if selection.get("__escalate__"):
                # RED-reversibility lever — never auto-execute, queue for owner
                red_items.append({
                    "kpi_name": kpi_name,
                    "brand": brand,
                    "status": status,
                    "lever_id": lever.get("id"),
                    "summary": lever.get("message") or f"{kpi_name}: RED escalation from runbook",
                })
                actions_taken.append({
                    "id": lever.get("id", "escalate"),
                    "kpi_name": kpi_name,
                    "brand": brand,
                    "escalated": True,
                })
            else:
                # v1: log the action as "would execute" — C2 wires the actual
                # spawn_agent / post_flag / config-change dispatchers with
                # adversarial review gating. Landing the persist path first
                # so we see the wake produce clean brief contributions before
                # any autonomous action fires.
                actions_taken.append({
                    "id": lever.get("id", "unknown"),
                    "action": lever.get("action"),
                    "target_agent": lever.get("target_agent"),
                    "kpi_name": kpi_name,
                    "brand": brand,
                    "reversibility": lever.get("reversibility"),
                    "impact": lever.get("impact"),
                    "executed": False,
                    "reason": "C1 v1 — persistence-only; C2 wires dispatch + adversarial reviewer",
                })

    duration_ms = int((time.monotonic() - started_at) * 1000)
    contribution = _compose_contribution(
        persona, scorecard, kpi_evals, actions_taken, red_items,
    )

    _persist_wake_event(persona, cadence, tally, actions_taken, duration_ms)
    _persist_brief_contribution(persona, contribution, red_items, actions_taken, kpi_evals)

    return {
        "persona": persona,
        "cadence": cadence,
        "kpis_evaluated": tally["kpis_evaluated"],
        "green": tally["green"],
        "yellow": tally["yellow"],
        "red": tally["red"],
        "no_data": tally["no_data"],
        "actions_taken": len(actions_taken),
        "red_items": len(red_items),
        "duration_ms": duration_ms,
    }


# ── Cadence dispatcher ──────────────────────────────────────────────────────


def wake_all_for_cadence(cadence: str) -> List[dict]:
    """Wake every persona whose scorecard cron_cadence matches. Called by
    APScheduler (one job per cadence bucket, mirroring metrics_collector).

    C1 only enables B&T. Other personas load their scorecards but skip the
    wake unless PERSONA_WAKE_ENABLED includes them (env-gated fan-out for
    C4/C5 rollout without another deploy per persona)."""
    import os as _os
    enabled = {p.strip().lower() for p in
               (_os.getenv("PERSONA_WAKE_ENABLED") or "bt").split(",") if p.strip()}

    summaries: List[dict] = []
    for path in sorted(SCORECARDS_DIR.glob("*.yaml")):
        persona = path.stem
        if persona not in enabled:
            continue
        try:
            with open(path) as h:
                sc = yaml.safe_load(h) or {}
        except Exception as e:
            logger.warning("[persona_wake] skip %s: bad YAML: %s", persona, e)
            continue
        if (sc.get("cron_cadence") or "").lower() != cadence.lower():
            continue
        try:
            summaries.append(wake(persona, cadence))
        except Exception as e:
            # Absolute last-resort catch — should not fire because wake()
            # is already defensive, but the scheduler stays alive no matter what
            logger.error("[persona_wake] wake(%s) raised: %s", persona, e)
            _persist_wake_event(persona, cadence, {"kpis_evaluated": 0}, [], 0, str(e)[:500])
    return summaries


if __name__ == "__main__":
    # Manual invocation: python -m services.persona_wake <cadence> [persona]
    import sys
    cadence = sys.argv[1] if len(sys.argv) > 1 else "every_4h"
    if len(sys.argv) > 2:
        result = wake(sys.argv[2], cadence)
    else:
        result = wake_all_for_cadence(cadence)
    print(json.dumps(result, indent=2, default=str))
