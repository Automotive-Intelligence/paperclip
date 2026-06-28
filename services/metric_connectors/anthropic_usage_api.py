"""anthropic_usage_api connector — B&T token_budget_burn_rate KPI.

NAMING NOTE: the bt.yaml scorecard names this source `anthropic_usage_api`
because the original plan was to hit Anthropic's Admin Usage API directly.
PR #88 (merged 2026-06-28) shipped a per-call llm_spend_ledger that captures
spend at the SDK call site for both the Anthropic path AND the CrewAI/litellm
path — that's more accurate, costs nothing extra, and covers all 22+ agents,
not just the persona-executor calls. So this connector reads from the
internal ledger via services.llm_ledger.daily_totals() instead of hitting
Anthropic's Admin API.

If a scorecard ever needs the Anthropic Admin numbers specifically (e.g.
account-level burn including unbilled cache hits), that can land as a
separate `anthropic_admin_api.py` connector. For now this is the source of
truth for B&T's daily token spend KPI.
"""

from datetime import datetime, timezone, timedelta
from typing import List

from services.metric_connectors.types import KPIReading


def fetch(kpi_spec: dict, run_ctx) -> List[KPIReading]:
    name = kpi_spec.get("name") or ""
    if name == "token_budget_burn_rate":
        return [_token_budget_burn_rate()]
    raise ValueError(f"anthropic_usage_api: unsupported kpi {name!r}")


def _token_budget_burn_rate() -> KPIReading:
    """Daily Claude spend in USD across all personas + agents.

    Reads from llm_spend_ledger (the canonical source PR #88 wired) — covers
    both Anthropic SDK calls (persona executor + reviewer) AND litellm calls
    (the 22+ worker agents). Returns the trailing-24h sum so B&T can see the
    rolling burn, not just calendar-day partials.
    """
    try:
        from services.llm_ledger import daily_totals
    except ImportError as e:
        return KPIReading(
            persona="bt",
            kpi_name="token_budget_burn_rate",
            status="connector_down",
            error_detail=f"llm_ledger import failed: {e}",
        )

    # Pull yesterday + today (UTC) and sum — covers the trailing-24h window
    # without a custom query. Cheap (one row-aggregate per day).
    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)

    try:
        today_data = daily_totals(today) or {}
        yest_data = daily_totals(yesterday) or {}
    except Exception as e:
        return KPIReading(
            persona="bt",
            kpi_name="token_budget_burn_rate",
            status="connector_down",
            error_detail=f"daily_totals query failed: {e}",
        )

    total_usd = float(today_data.get("total_usd") or 0.0) + float(yest_data.get("total_usd") or 0.0)
    total_calls = int(today_data.get("calls") or 0) + int(yest_data.get("calls") or 0)

    if total_calls == 0:
        # Brand-new install, ledger empty, or first-day startup. Surface
        # as no_data rather than "$0 perfect score" which would mislead.
        return KPIReading(
            persona="bt",
            kpi_name="token_budget_burn_rate",
            status="no_data",
            error_detail="ledger empty over last 48h — no Claude calls recorded",
            raw_payload={"today": today_data, "yesterday": yest_data},
        )

    return KPIReading(
        persona="bt",
        kpi_name="token_budget_burn_rate",
        value_numeric=round(total_usd, 4),
        unit="USD (trailing 24h)",
        raw_payload={
            "calls_24h": total_calls,
            "today_usd": round(float(today_data.get("total_usd") or 0.0), 4),
            "yesterday_usd": round(float(yest_data.get("total_usd") or 0.0), 4),
            "by_persona": today_data.get("by_persona", [])[:10],  # top 10 for the brief
            "by_model": today_data.get("by_model", [])[:5],
            "source_table": "llm_spend_ledger (PR #88)",
        },
    )
