"""twenty_client_status connector — B2B Operations client KPIs.

Sources:
  - b2b_operations.client_health_score — composite per-client health derived
    from Twenty CRM company status (active/at-risk/churned/paused).
  - b2b_operations.client_churn_rate — net client change in trailing 30 days.

Reads via tools/twenty.py per-workspace API. Counts companies tagged as
clients (presence of an opportunity in won/customer stage on any workspace).
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import requests

from services.metric_connectors.types import KPIReading
from tools.twenty import _workspace_config, twenty_ready, _headers, _REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

# Which Twenty workspaces house active B2B clients today. AvI has dealer
# customers, WD has SMB clients, Book'd has agent customers. Add as new
# brands acquire customers.
CLIENT_WORKSPACES = ["callingdigital", "autointelligence", "bookd"]


def fetch(kpi_spec: dict, run_ctx) -> List[KPIReading]:
    name = kpi_spec.get("name") or ""
    if name == "client_health_score":
        return [_client_health_score()]
    if name == "client_churn_rate":
        return [_client_churn_rate()]
    raise ValueError(f"twenty_client_status: unsupported kpi {name!r}")


def _list_companies(biz_key: str, limit: int = 100) -> list:
    base_url, api_key = _workspace_config(biz_key)
    r = requests.get(
        f"{base_url}/rest/companies",
        headers=_headers(api_key),
        params={"limit": limit, "order_by": "updatedAt[DescNullsLast]"},
        timeout=_REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    return (r.json().get("data") or {}).get("companies") or []


def _list_opportunities_for(biz_key: str, limit: int = 200) -> list:
    base_url, api_key = _workspace_config(biz_key)
    r = requests.get(
        f"{base_url}/rest/opportunities",
        headers=_headers(api_key),
        params={"limit": limit, "order_by": "updatedAt[DescNullsLast]"},
        timeout=_REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    return (r.json().get("data") or {}).get("opportunities") or []


def _client_health_score() -> KPIReading:
    """Avg client health 0-100 across workspaces. v1 heuristic — boolean active:
    a company counts as "healthy" if its latest opportunity stage is 'customer'
    or 'won' and updatedAt is within 30 days. As the brief lands we'll tune
    against real per-client signals (engagement, payment, sentiment).
    """
    healthy = 0
    total = 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    per_workspace: dict = {}
    errors: list = []

    for biz_key in CLIENT_WORKSPACES:
        if not twenty_ready(biz_key):
            per_workspace[biz_key] = {"status": "workspace_not_configured"}
            continue
        try:
            opps = _list_opportunities_for(biz_key)
        except Exception as e:
            errors.append({"workspace": biz_key, "error": str(e)[:200]})
            per_workspace[biz_key] = {"status": "error"}
            continue
        ws_healthy = 0
        ws_total = 0
        for o in opps:
            stage = (o.get("stage") or "").lower()
            if stage not in ("customer", "won", "closed_won"):
                continue
            ws_total += 1
            updated = o.get("updatedAt") or ""
            try:
                dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt >= cutoff:
                    ws_healthy += 1
            except ValueError:
                pass
        per_workspace[biz_key] = {"total_customers": ws_total, "healthy_30d": ws_healthy}
        healthy += ws_healthy
        total += ws_total

    if total == 0:
        return KPIReading(
            persona="b2b_operations",
            kpi_name="client_health_score",
            status="no_data",
            raw_payload={"per_workspace": per_workspace, "errors": errors},
        )

    score = round(100.0 * healthy / total, 2)
    return KPIReading(
        persona="b2b_operations",
        kpi_name="client_health_score",
        value_numeric=score,
        unit="score / 100 avg",
        raw_payload={
            "total_customers": total,
            "healthy_30d": healthy,
            "per_workspace": per_workspace,
            "definition": "v1 — boolean 30-day-active; tune as engagement signals land",
        },
    )


def _client_churn_rate() -> KPIReading:
    """Net customer change over the last 30 days (gained - lost across all
    workspaces). Negative = churn."""
    cutoff_30d = datetime.now(timezone.utc) - timedelta(days=30)
    gained = 0
    lost = 0
    per_workspace: dict = {}
    errors: list = []

    for biz_key in CLIENT_WORKSPACES:
        if not twenty_ready(biz_key):
            per_workspace[biz_key] = {"status": "workspace_not_configured"}
            continue
        try:
            opps = _list_opportunities_for(biz_key, limit=300)
        except Exception as e:
            errors.append({"workspace": biz_key, "error": str(e)[:200]})
            per_workspace[biz_key] = {"status": "error"}
            continue

        ws_gained = 0
        ws_lost = 0
        for o in opps:
            stage = (o.get("stage") or "").lower()
            updated = o.get("updatedAt") or ""
            try:
                dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if dt < cutoff_30d:
                continue
            if stage in ("won", "closed_won", "customer"):
                ws_gained += 1
            elif stage in ("churned", "lost", "closed_lost", "paused"):
                ws_lost += 1
        per_workspace[biz_key] = {"gained_30d": ws_gained, "lost_30d": ws_lost}
        gained += ws_gained
        lost += ws_lost

    net = gained - lost
    return KPIReading(
        persona="b2b_operations",
        kpi_name="client_churn_rate",
        value_numeric=float(net),
        unit="net clients / 30d",
        raw_payload={
            "window_days": 30,
            "gained": gained,
            "lost": lost,
            "net": net,
            "per_workspace": per_workspace,
            "errors": errors,
        },
    )
