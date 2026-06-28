"""vercel_inventory connector — contributes to B&T api_uptime_per_service.

Reads Vercel project inventory + deployment health. Cross-cutting with the
existing cto_daily_sweep which writes a JSON snapshot — this connector reads
that snapshot (if fresh) OR falls back to a live Vercel API call.

Uses VERCEL_API_TOKEN (already wired post the recent token refresh per
infrastructure_state.md). Skip-graceful if absent.

Phase B1b ships only the inventory pull. The composite api_uptime score is
owned by cto_daily_sweep connector — this one writes a per-project breakdown
that the brief can drill into.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List

import requests

from services.metric_connectors.types import KPIReading

logger = logging.getLogger(__name__)

_API_BASE = "https://api.vercel.com"
_REQUEST_TIMEOUT = 12
_PER_PAGE = 50


def fetch(kpi_spec: dict, run_ctx) -> List[KPIReading]:
    """vercel_inventory only currently surfaces a portfolio_health-style
    composite — count of healthy vs unhealthy projects. The bt scorecard
    api_uptime_per_service KPI is owned by cto_daily_sweep, not here.
    Keeping this connector live so b2b_operations.avo_workflow_uptime and
    future Vercel-backed KPIs have a place to land.
    """
    name = kpi_spec.get("name") or ""
    if name == "vercel_project_health":
        return [_vercel_project_health()]
    raise ValueError(f"vercel_inventory: unsupported kpi {name!r}")


def _vercel_project_health() -> KPIReading:
    token = (os.getenv("VERCEL_API_TOKEN") or "").strip()
    if not token:
        return KPIReading(
            persona="bt",
            kpi_name="vercel_project_health",
            status="connector_down",
            error_detail="VERCEL_API_TOKEN not set",
        )
    team_id = (os.getenv("VERCEL_TEAM_ID") or "").strip()
    headers = {"Authorization": f"Bearer {token}"}
    params: dict = {"limit": _PER_PAGE}
    if team_id:
        params["teamId"] = team_id

    try:
        r = requests.get(f"{_API_BASE}/v9/projects", headers=headers, params=params, timeout=_REQUEST_TIMEOUT)
        r.raise_for_status()
        projects = (r.json() or {}).get("projects") or []
    except Exception as e:
        return KPIReading(
            persona="bt",
            kpi_name="vercel_project_health",
            status="connector_down",
            error_detail=str(e)[:300],
        )

    if not projects:
        return KPIReading(
            persona="bt",
            kpi_name="vercel_project_health",
            status="no_data",
            raw_payload={"team_id": team_id or "personal"},
        )

    healthy = 0
    erroring = 0
    stale_days_120 = 0  # match the threshold from infrastructure_sweep PR #62
    cutoff_120d = datetime.now(timezone.utc) - timedelta(days=120)

    breakdown: list = []
    for proj in projects:
        name = proj.get("name", "")
        latest = (proj.get("latestDeployments") or [None])[0] or {}
        state = (latest.get("readyState") or latest.get("state") or "").upper()
        created_ms = latest.get("created") or 0
        last_deploy = datetime.fromtimestamp(created_ms / 1000.0, tz=timezone.utc) if created_ms else None
        is_stale = last_deploy is not None and last_deploy < cutoff_120d

        if state in ("ERROR", "CANCELED"):
            erroring += 1
        elif state in ("READY", "BUILDING", "QUEUED", "INITIALIZING"):
            healthy += 1
        if is_stale:
            stale_days_120 += 1

        breakdown.append({
            "name": name,
            "state": state or "unknown",
            "stale_120d": is_stale,
        })

    total = len(projects)
    pct_healthy = round(100.0 * healthy / total, 2) if total else 0.0

    return KPIReading(
        persona="bt",
        kpi_name="vercel_project_health",
        value_numeric=pct_healthy,
        unit="%",
        raw_payload={
            "total_projects": total,
            "healthy": healthy,
            "erroring": erroring,
            "stale_120d": stale_days_120,
            "breakdown": breakdown,
        },
    )
