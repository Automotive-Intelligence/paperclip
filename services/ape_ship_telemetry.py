"""Pre/post metric correlation for autonomous ships.

When a ship lands, snapshot a handful of "system health" metrics
(agent run counts, error counts, outbound send count). 24h later,
re-snapshot. If a metric regressed beyond threshold, write a row
to autonomous_ship_telemetry with flagged=true; the daily CTO sweep
surfaces these as autonomous_ship_health findings.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Optional

logger = logging.getLogger(__name__)

REGRESSION_THRESHOLD_PCT = 25.0   # >25% drop in a metric triggers a flag


def _snapshot_metrics() -> Dict[str, float]:
    """Return current values of key system-health metrics."""
    try:
        from services.database import fetch_all
        m: Dict[str, float] = {}
        rows = fetch_all(
            "SELECT COUNT(*) FROM agent_logs WHERE created_at >= NOW() - INTERVAL '24 hours'"
        )
        m["agent_runs_24h"] = float(rows[0][0])
        rows = fetch_all(
            """
            SELECT COUNT(*) FROM agent_logs
            WHERE created_at >= NOW() - INTERVAL '24 hours'
              AND (LOWER(content) LIKE '%error%' OR LOWER(content) LIKE '%exception%')
            """
        )
        m["agent_errors_24h"] = float(rows[0][0])
        return m
    except Exception as e:
        logger.warning(f"[ape:ship_telemetry] snapshot failed: {e}")
        return {}


def record_pre_snapshot(handoff_id: int, ship_id: str, persona: str) -> None:
    """Capture pre-ship metrics immediately before the ship completes."""
    metrics = _snapshot_metrics()
    if not metrics:
        return
    try:
        import psycopg2
        from services.database import _get_url
        now = datetime.now(timezone.utc)
        conn = psycopg2.connect(_get_url())
        cur = conn.cursor()
        for name, value in metrics.items():
            cur.execute(
                """
                INSERT INTO autonomous_ship_telemetry
                  (handoff_id, ship_id, persona, metric_name, pre_value, pre_taken_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (handoff_id, ship_id, persona, name, value, now),
            )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.warning(f"[ape:ship_telemetry] pre snapshot insert failed: {e}")


def record_post_snapshots_and_flag() -> int:
    """For each ship without a post snapshot whose pre snapshot is >=24h old,
    capture post and compute delta. Returns count of ships flagged."""
    try:
        import psycopg2
        from services.database import _get_url, fetch_all
        rows = fetch_all(
            """
            SELECT id, ship_id, persona, metric_name, pre_value
            FROM autonomous_ship_telemetry
            WHERE post_value IS NULL
              AND pre_taken_at <= NOW() - INTERVAL '24 hours'
            """
        )
    except Exception as e:
        logger.warning(f"[ape:ship_telemetry] post sweep query failed: {e}")
        return 0

    if not rows:
        return 0

    current = _snapshot_metrics()
    flagged_count = 0
    try:
        import psycopg2
        from services.database import _get_url
        conn = psycopg2.connect(_get_url())
        cur = conn.cursor()
        for row_id, ship_id, persona, metric_name, pre_value in rows:
            post_value = current.get(metric_name)
            if post_value is None or pre_value is None:
                continue
            delta_pct = (
                ((post_value - pre_value) / pre_value * 100.0)
                if pre_value > 0
                else 0.0
            )
            flagged = delta_pct <= -REGRESSION_THRESHOLD_PCT
            cur.execute(
                """
                UPDATE autonomous_ship_telemetry
                SET post_value = %s, post_taken_at = NOW(), delta_pct = %s, flagged = %s
                WHERE id = %s
                """,
                (post_value, delta_pct, flagged, row_id),
            )
            if flagged:
                flagged_count += 1
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.warning(f"[ape:ship_telemetry] post sweep update failed: {e}")
    return flagged_count
