"""postgres_agent_logs connector — reads internal agent_logs / agent_handoffs.

Source for B&T scorecard KPIs:
  - agent_error_rate          — % of last-24h runs that errored
  - agent_run_completion_rate — % of last-24h runs that reached completion
  - time_to_resolution_flagged_issues — median hours flag-posted → flag-closed

Reuses the SAME tight ERROR_REGEX that PR #58 introduced for infrastructure_sweep
(after the LIKE-based detector's emails_failed:0 false-positive disaster). Word
boundaries on keywords, HTTP-status code anchoring. Single source of truth for
"is this log a real error" — both surfaces agree by construction.
"""

from typing import List

from services.database import fetch_all
# Reuse the audited error regex from the sweep so the two error-detector
# surfaces never drift apart. If we patch the regex over there for a new
# false-positive class, we get the benefit here for free.
from services.infrastructure_sweep import ERROR_REGEX
from services.metric_connectors.types import KPIReading


# Cadence we expect in scorecards. Connector itself is cadence-agnostic; this
# constant just documents what bt.yaml says.
WINDOW_HOURS_24 = 24


def fetch(kpi_spec: dict, run_ctx) -> List[KPIReading]:
    name = kpi_spec.get("name") or ""
    if name == "agent_error_rate":
        return [_agent_error_rate()]
    if name == "agent_run_completion_rate":
        return [_agent_run_completion_rate()]
    if name == "time_to_resolution_flagged_issues":
        return [_time_to_resolution()]
    # Unknown KPI for this source — bubble up as connector_down so it's loud,
    # not a silent "no_data".
    raise ValueError(f"postgres_agent_logs: unsupported kpi name {name!r}")


def _agent_error_rate() -> KPIReading:
    """% of (last-24h) agent_logs rows that match the real-error regex."""
    rows = fetch_all(
        f"""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE content ~ %s) AS errored
        FROM agent_logs
        WHERE created_at > NOW() - INTERVAL '{WINDOW_HOURS_24} hours'
        """,
        (ERROR_REGEX,),
    )
    if not rows:
        return KPIReading(persona="bt", kpi_name="agent_error_rate", status="no_data")
    # fetch_all returns tuples — positional indexing matches the SELECT order
    # (COUNT(*) AS total, COUNT(*) FILTER (...) AS errored)
    total = int(rows[0][0] or 0)
    errored = int(rows[0][1] or 0)
    if total == 0:
        # No runs in the last 24h is itself an anomaly worth seeing as "no_data"
        # vs reporting a meaningless 0%.
        return KPIReading(persona="bt", kpi_name="agent_error_rate", status="no_data",
                          raw_payload={"total": 0, "errored": 0})
    pct = round(100.0 * errored / total, 3)
    return KPIReading(
        persona="bt",
        kpi_name="agent_error_rate",
        value_numeric=pct,
        unit="%",
        raw_payload={"window_hours": WINDOW_HOURS_24, "total_runs": total, "errored": errored},
    )


def _agent_run_completion_rate() -> KPIReading:
    """% of last-24h agent runs that reached terminal completion vs timed-out/hung.

    Definition: an agent run is considered "complete" if its run row has a
    status of 'completed' or 'success'. Anything in 'running', 'timeout',
    'crashed', NULL after the window cutoff counts as not-completed.

    We tolerate either an `agent_runs` table OR (fallback) inferring from
    agent_logs paired with completion markers — but the schema-of-record is
    agent_runs per project memory. If that table is unavailable we return
    schema_drift loudly so B&T sees it.
    """
    try:
        rows = fetch_all(
            f"""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE LOWER(status) IN ('completed', 'success', 'ok')) AS completed
            FROM agent_runs
            WHERE started_at > NOW() - INTERVAL '{WINDOW_HOURS_24} hours'
            """
        )
    except Exception as e:
        # agent_runs may not exist in some envs (dev clones, fresh installs)
        return KPIReading(
            persona="bt",
            kpi_name="agent_run_completion_rate",
            status="schema_drift",
            error_detail=f"agent_runs unavailable: {e}",
        )
    if not rows:
        return KPIReading(persona="bt", kpi_name="agent_run_completion_rate", status="no_data")
    # Positional: SELECT order is (total, completed)
    total = int(rows[0][0] or 0)
    completed = int(rows[0][1] or 0)
    if total == 0:
        return KPIReading(persona="bt", kpi_name="agent_run_completion_rate", status="no_data",
                          raw_payload={"total": 0})
    pct = round(100.0 * completed / total, 3)
    return KPIReading(
        persona="bt",
        kpi_name="agent_run_completion_rate",
        value_numeric=pct,
        unit="%",
        raw_payload={"window_hours": WINDOW_HOURS_24, "total_runs": total, "completed": completed},
    )


def _time_to_resolution() -> KPIReading:
    """Median hours from agent_handoffs.created_at to closed_at (last 7d window)."""
    try:
        rows = fetch_all(
            """
            SELECT
                PERCENTILE_CONT(0.5) WITHIN GROUP (
                    ORDER BY EXTRACT(EPOCH FROM (closed_at - created_at)) / 3600.0
                ) AS median_hours,
                COUNT(*) AS closed_count
            FROM agent_handoffs
            WHERE closed_at IS NOT NULL
              AND closed_at > NOW() - INTERVAL '7 days'
            """
        )
    except Exception as e:
        return KPIReading(
            persona="bt",
            kpi_name="time_to_resolution_flagged_issues",
            status="schema_drift",
            error_detail=str(e),
        )
    # Positional: SELECT order is (median_hours, closed_count)
    if not rows or rows[0][0] is None:
        return KPIReading(
            persona="bt",
            kpi_name="time_to_resolution_flagged_issues",
            status="no_data",
            raw_payload={"closed_count": int(rows[0][1] or 0) if rows else 0},
        )
    median_hours = float(rows[0][0])
    closed_count = int(rows[0][1] or 0)
    return KPIReading(
        persona="bt",
        kpi_name="time_to_resolution_flagged_issues",
        value_numeric=round(median_hours, 2),
        unit="hours (median)",
        raw_payload={"window_days": 7, "closed_count": closed_count},
    )
