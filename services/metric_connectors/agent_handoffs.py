"""agent_handoffs connector — Pit Wall + B2B Ops KPIs sourced from the
agent_handoffs table.

Surfaces:
  - pit_wall.cross_persona_dependency_resolution_hours — median resolution time
    on handoffs where from_agent != to_agent (cross-persona)
  - pit_wall.decisions_blocking_execution — count of RED-tier handoffs older
    than 48 hours and not yet closed (the Owner-Brief escalation surface)
  - pit_wall.focus_arbitration_outcomes — % of pit_wall-originated handoffs
    that resulted in another persona acting (vs noise)

Reuses ape_impact_tier + ape_reversibility columns introduced by the APE
Phase 1 migration (services/persona_executor.py:280-281).
"""

from typing import List

from services.database import fetch_all
from services.metric_connectors.types import KPIReading


def fetch(kpi_spec: dict, run_ctx) -> List[KPIReading]:
    name = kpi_spec.get("name") or ""
    if name == "cross_persona_dependency_resolution_hours":
        return [_cross_persona_resolution_median()]
    if name == "decisions_blocking_execution":
        return [_red_tier_blocking_count()]
    if name == "focus_arbitration_outcomes":
        return [_pit_wall_arbitration_outcomes()]
    raise ValueError(f"agent_handoffs: unsupported kpi {name!r}")


def _cross_persona_resolution_median() -> KPIReading:
    """Median hours from posted_at to closed_at on cross-persona handoffs
    (from_agent != to_agent) closed in the last 7 days."""
    try:
        rows = fetch_all(
            """
            SELECT
                PERCENTILE_CONT(0.5) WITHIN GROUP (
                    ORDER BY EXTRACT(EPOCH FROM (completed_at - created_at)) / 3600.0
                ) AS median_hours,
                COUNT(*) AS closed_count
            FROM agent_handoffs
            WHERE completed_at IS NOT NULL
              AND completed_at > NOW() - INTERVAL '7 days'
              AND from_agent IS NOT NULL
              AND to_agent IS NOT NULL
              AND from_agent != to_agent
            """
        )
    except Exception as e:
        return KPIReading(
            persona="pit_wall",
            kpi_name="cross_persona_dependency_resolution_hours",
            status="schema_drift",
            error_detail=str(e),
        )
    if not rows or rows[0][0] is None:
        return KPIReading(
            persona="pit_wall",
            kpi_name="cross_persona_dependency_resolution_hours",
            status="no_data",
            raw_payload={"closed_count": int(rows[0][1] or 0) if rows else 0, "window_days": 7},
        )
    return KPIReading(
        persona="pit_wall",
        kpi_name="cross_persona_dependency_resolution_hours",
        value_numeric=round(float(rows[0][0]), 2),
        unit="hours (median)",
        raw_payload={"closed_count": int(rows[0][1] or 0), "window_days": 7},
    )


def _red_tier_blocking_count() -> KPIReading:
    """Count of RED-tier handoffs older than 48 hours with status != 'complete'.

    RED means the persona-cron-loop's adversarial reviewer escalated this to
    Michael and no auto-ship is permitted. Anything >48h is overdue.
    """
    try:
        rows = fetch_all(
            """
            SELECT COUNT(*)
            FROM agent_handoffs
            WHERE ape_reversibility = 'RED'
              AND status != 'complete'
              AND created_at < NOW() - INTERVAL '48 hours'
            """
        )
    except Exception as e:
        # ape_reversibility may not exist in some envs — degrade to plain status
        try:
            rows = fetch_all(
                """
                SELECT COUNT(*)
                FROM agent_handoffs
                WHERE status = 'pending'
                  AND created_at < NOW() - INTERVAL '48 hours'
                """
            )
        except Exception as e2:
            return KPIReading(
                persona="pit_wall",
                kpi_name="decisions_blocking_execution",
                status="schema_drift",
                error_detail=str(e2),
            )
    count = int(rows[0][0] or 0) if rows else 0
    return KPIReading(
        persona="pit_wall",
        kpi_name="decisions_blocking_execution",
        value_numeric=float(count),
        unit="count",
        raw_payload={"age_threshold_hours": 48, "tier_filter": "RED"},
    )


def _pit_wall_arbitration_outcomes() -> KPIReading:
    """% of pit_wall-originated handoffs (last 7d) that reached 'complete'
    vs the total — proxy for "did the arbitration call move a stalled lane."
    """
    try:
        rows = fetch_all(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = 'complete') AS completed
            FROM agent_handoffs
            WHERE from_agent = 'pit_wall'
              AND created_at > NOW() - INTERVAL '7 days'
            """
        )
    except Exception as e:
        return KPIReading(
            persona="pit_wall",
            kpi_name="focus_arbitration_outcomes",
            status="schema_drift",
            error_detail=str(e),
        )
    if not rows:
        return KPIReading(
            persona="pit_wall",
            kpi_name="focus_arbitration_outcomes",
            status="no_data",
        )
    total = int(rows[0][0] or 0)
    completed = int(rows[0][1] or 0)
    if total == 0:
        return KPIReading(
            persona="pit_wall",
            kpi_name="focus_arbitration_outcomes",
            status="no_data",
            raw_payload={"total": 0, "window_days": 7},
        )
    pct = round(100.0 * completed / total, 2)
    return KPIReading(
        persona="pit_wall",
        kpi_name="focus_arbitration_outcomes",
        value_numeric=pct,
        unit="%",
        raw_payload={"total": total, "completed": completed, "window_days": 7},
    )
