# AVO — AI Business Operating System
# Cost Monitoring — Token and dollar tracking for every agent run
# Built live for Agent Empire Skool community
# Salesdroid — April 2026

"""Cost tracking for all AVO agent runs.

Tracks token usage and cost per run. Alerts Michael via Twilio
if any single run exceeds $5 or daily total exceeds $20.

PostgreSQL table: agent_run_costs
"""

import json
import logging
import time
from datetime import datetime, date, timezone
from typing import Any, Dict, Optional

from services.database import execute_query, fetch_all
from services.errors import DatabaseError

logger = logging.getLogger(__name__)

# Anthropic pricing (as of April 2026)
# Claude Sonnet 4.6
PRICING = {
    "claude-sonnet-4-6": {"input_per_m": 3.00, "output_per_m": 15.00},
    "claude-sonnet-4-5-20241022": {"input_per_m": 3.00, "output_per_m": 15.00},
    "default": {"input_per_m": 3.00, "output_per_m": 15.00},
}

# Alert thresholds
SINGLE_RUN_ALERT_USD = 5.00
DAILY_TOTAL_ALERT_USD = 20.00


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Calculate cost in USD for a given run."""
    rates = PRICING.get(model, PRICING["default"])
    input_cost = (input_tokens / 1_000_000) * rates["input_per_m"]
    output_cost = (output_tokens / 1_000_000) * rates["output_per_m"]
    return round(input_cost + output_cost, 6)


def log_run_cost(
    agent_name: str,
    river: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    run_duration_seconds: float = 0.0,
) -> Optional[float]:
    """Log a single agent run's cost to PostgreSQL.

    Returns:
        cost_usd if logged, None on error.
    """
    cost_usd = calculate_cost(model, input_tokens, output_tokens)

    try:
        execute_query(
            "INSERT INTO agent_run_costs "
            "(agent_name, river, model, input_tokens, output_tokens, "
            "cost_usd, run_date, run_duration_seconds) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (agent_name, river, model, input_tokens, output_tokens,
             cost_usd, date.today(), run_duration_seconds),
        )
        logger.info(
            "[Cost] %s: %d in / %d out = $%.4f (%s)",
            agent_name, input_tokens, output_tokens, cost_usd, model,
        )

        # Check single-run alert
        if cost_usd >= SINGLE_RUN_ALERT_USD:
            _send_cost_alert(
                f"COST ALERT: {agent_name} just spent ${cost_usd:.2f} in one run. Check logs."
            )

        # Check daily total alert
        daily = get_daily_cost()
        if daily >= DAILY_TOTAL_ALERT_USD:
            _send_cost_alert(
                f"AVO DAILY COST: ${daily:.2f}. Review agent_run_costs table."
            )

        return cost_usd
    except DatabaseError as e:
        logger.warning("[Cost] log_run_cost failed for %s: %s", agent_name, e)
        return None


def _send_cost_alert(message: str):
    """Send cost alert via notifier (Twilio to Michael)."""
    try:
        from core.notifier import notify_cost_alert
        notify_cost_alert(message)
    except Exception as e:
        logger.error("[Cost] Alert send failed: %s", e)


def get_daily_cost(target_date: Optional[date] = None) -> float:
    """Get total cost for a given date (defaults to today)."""
    if target_date is None:
        target_date = date.today()
    try:
        rows = fetch_all(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM agent_run_costs "
            "WHERE run_date = %s",
            (target_date,),
        )
        return float(rows[0][0]) if rows else 0.0
    except DatabaseError:
        return 0.0


def get_agent_cost_summary(days: int = 7) -> list:
    """Get cost summary per agent over N days."""
    try:
        rows = fetch_all(
            "SELECT agent_name, SUM(input_tokens), SUM(output_tokens), "
            "SUM(cost_usd), COUNT(*) "
            "FROM agent_run_costs "
            "WHERE run_date >= CURRENT_DATE - INTERVAL '%s days' "
            "GROUP BY agent_name ORDER BY SUM(cost_usd) DESC",
            (days,),
        )
        return [
            {
                "agent_name": r[0],
                "total_input_tokens": int(r[1] or 0),
                "total_output_tokens": int(r[2] or 0),
                "total_cost_usd": round(float(r[3] or 0), 4),
                "total_runs": int(r[4] or 0),
            }
            for r in rows
        ]
    except DatabaseError:
        return []


def get_river_cost_summary(days: int = 7) -> list:
    """Get cost summary per river over N days."""
    try:
        rows = fetch_all(
            "SELECT river, SUM(cost_usd), COUNT(*) "
            "FROM agent_run_costs "
            "WHERE run_date >= CURRENT_DATE - INTERVAL '%s days' "
            "GROUP BY river ORDER BY SUM(cost_usd) DESC",
            (days,),
        )
        return [
            {
                "river": r[0],
                "total_cost_usd": round(float(r[1] or 0), 4),
                "total_runs": int(r[2] or 0),
            }
            for r in rows
        ]
    except DatabaseError:
        return []


def get_monthly_projection() -> Dict[str, float]:
    """Project monthly cost based on last 7 days average."""
    try:
        rows = fetch_all(
            "SELECT COALESCE(SUM(cost_usd), 0), "
            "COUNT(DISTINCT run_date) "
            "FROM agent_run_costs "
            "WHERE run_date >= CURRENT_DATE - INTERVAL '7 days'"
        )
        if not rows or not rows[0][1]:
            return {"daily_average": 0.0, "projected_monthly": 0.0}
        total = float(rows[0][0] or 0)
        active_days = int(rows[0][1] or 1)
        daily_avg = total / max(active_days, 1)
        return {
            "daily_average": round(daily_avg, 4),
            "projected_monthly": round(daily_avg * 30, 2),
        }
    except DatabaseError:
        return {"daily_average": 0.0, "projected_monthly": 0.0}


def get_cost_by_day(days: int = 7) -> list:
    """Get cost totals per day for the last N days (for time-series charts)."""
    try:
        rows = fetch_all(
            "SELECT run_date, COALESCE(SUM(cost_usd), 0), COUNT(*) "
            "FROM agent_run_costs "
            "WHERE run_date >= CURRENT_DATE - INTERVAL '%s days' "
            "GROUP BY run_date ORDER BY run_date ASC",
            (days,),
        )
        return [
            {
                "date": str(r[0]),
                "total_usd": round(float(r[1] or 0), 4),
                "run_count": int(r[2] or 0),
            }
            for r in rows
        ]
    except DatabaseError:
        return []


def get_most_expensive_run(days: int = 7) -> Optional[Dict[str, Any]]:
    """Get the single most expensive run in the last N days."""
    try:
        rows = fetch_all(
            "SELECT agent_name, river, model, input_tokens, output_tokens, "
            "cost_usd, run_date, run_duration_seconds "
            "FROM agent_run_costs "
            "WHERE run_date >= CURRENT_DATE - INTERVAL '%s days' "
            "ORDER BY cost_usd DESC LIMIT 1",
            (days,),
        )
        if not rows:
            return None
        r = rows[0]
        return {
            "agent_name": r[0],
            "river": r[1],
            "model": r[2],
            "input_tokens": int(r[3] or 0),
            "output_tokens": int(r[4] or 0),
            "cost_usd": round(float(r[5] or 0), 4),
            "run_date": str(r[6]),
            "run_duration_seconds": float(r[7] or 0),
        }
    except DatabaseError:
        return None


class CostTimer:
    """Context manager to time agent runs for cost tracking."""

    def __init__(self):
        self.start_time = None
        self.duration = 0.0

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, *args):
        self.duration = time.time() - self.start_time
