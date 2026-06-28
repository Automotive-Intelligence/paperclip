"""scorecard_aggregator connector — Pit Wall's portfolio-health composite.

Pit Wall is the Chief Strategist. Its KPI portfolio_health_index is a meta-KPI:
the average green-rate across all OTHER personas' KPIs. Without this connector,
Pit Wall has nothing to brief Michael on except its own narrow surface.

Reads the latest snapshot per (persona, kpi_name, brand) from kpi_snapshots,
classifies each as green/yellow/red against the snapshot's status + (when we
have it) the scorecard's threshold_red / threshold_yellow, computes
average-green-rate across all personas.

Phase B1c v1 — simple status-based scoring:
  green   = status='ok'
  yellow  = status in ('no_data', 'stale')
  red     = status in ('connector_down', 'schema_drift', 'rate_limited', 'timeout')

Phase C will upgrade this to compare value_numeric vs scorecard threshold_red/yellow
once personas wake and read scorecards together with snapshots. v1 surfaces
"how many of our KPIs are actually getting data" — that alone is the right
Pit-Wall pulse for the first weeks.
"""

import logging
from typing import List

from services.database import fetch_all
from services.metric_connectors.types import KPIReading

logger = logging.getLogger(__name__)


def fetch(kpi_spec: dict, run_ctx) -> List[KPIReading]:
    name = kpi_spec.get("name") or ""
    if name == "portfolio_health_index":
        return [_portfolio_health_index()]
    if name == "strategic_bet_allocation":
        # Placeholder — needs telemetry/strategic_calls.md parser; deferred
        return [KPIReading(
            persona="pit_wall",
            kpi_name="strategic_bet_allocation",
            status="connector_down",
            error_detail="telemetry_strategic_calls parser not implemented yet",
        )]
    raise ValueError(f"scorecard_aggregator: unsupported kpi {name!r}")


def _portfolio_health_index() -> KPIReading:
    """Composite 0-100 across all personas, derived from the latest snapshot
    of every (persona, kpi_name, brand) tuple in the last 25h window.

    The 25h window catches all hourly + daily cadences. Weekly KPIs falling
    outside the window count toward 'stale' which drags the score down — by
    design, Pit Wall should see when a slice of the portfolio hasn't been
    measured recently."""
    try:
        rows = fetch_all(
            """
            WITH latest AS (
                SELECT DISTINCT ON (persona, kpi_name, COALESCE(brand, ''))
                    persona, kpi_name, brand, status, ts_collected
                FROM kpi_snapshots
                WHERE ts_collected > NOW() - INTERVAL '25 hours'
                ORDER BY persona, kpi_name, COALESCE(brand, ''), ts_collected DESC
            )
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = 'ok') AS green,
                COUNT(*) FILTER (WHERE status IN ('no_data', 'stale')) AS yellow,
                COUNT(*) FILTER (WHERE status NOT IN ('ok', 'no_data', 'stale')) AS red,
                COUNT(DISTINCT persona) AS personas_with_data
            FROM latest
            """
        )
    except Exception as e:
        return KPIReading(
            persona="pit_wall",
            kpi_name="portfolio_health_index",
            status="schema_drift",
            error_detail=str(e),
        )
    if not rows or int(rows[0][0] or 0) == 0:
        return KPIReading(
            persona="pit_wall",
            kpi_name="portfolio_health_index",
            status="no_data",
            error_detail="no snapshots in last 25h — collector may not be running yet",
        )

    total = int(rows[0][0])
    green = int(rows[0][1] or 0)
    yellow = int(rows[0][2] or 0)
    red = int(rows[0][3] or 0)
    personas_with_data = int(rows[0][4] or 0)

    # Weighted score: green=1.0, yellow=0.5, red=0.0
    score = round(100.0 * (green + 0.5 * yellow) / total, 2)

    return KPIReading(
        persona="pit_wall",
        kpi_name="portfolio_health_index",
        value_numeric=score,
        unit="0-100",
        raw_payload={
            "window_hours": 25,
            "total_kpis_with_recent_snapshot": total,
            "green": green,
            "yellow": yellow,
            "red": red,
            "personas_with_data": personas_with_data,
            "scoring": "green=1.0 + yellow=0.5 + red=0.0 → /total *100",
        },
    )
