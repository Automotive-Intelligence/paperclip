"""twenty_opportunity_log connector — CRO opportunity + pipeline metrics.

Reads Twenty REST per business-workspace (wd, avi, bookd). Reuses the same
per-workspace config + auth pattern as tools/twenty.py so we don't duplicate
URL/key resolution logic.

Source for CRO scorecard KPIs:
  - new_opportunities_per_week — opportunities created per week per brand
  - pipeline_coverage_ratio    — open-pipeline value / remaining quarterly target
  - pipeline_velocity_days     — median days from first-touch to close-won

Per-brand fanout — each fetch() returns one KPIReading per brand the KPI lists.
Brands without a configured Twenty workspace (e.g. bookd before Ryan's key
lands) get a status='no_data' reading flagged with reason — no exception.
"""

from datetime import datetime, timezone
from statistics import median
from typing import List, Optional

import requests

from services.metric_connectors.types import KPIReading
from tools.twenty import _workspace_config, twenty_ready, _headers, _REQUEST_TIMEOUT


def _parse_twenty_ts(raw: str) -> Optional[float]:
    """Twenty returns ISO datetimes for createdAt and bare YYYY-MM-DD for closeDate.
    Parse either, force UTC if naive (Twenty's stored values are UTC), return
    epoch-seconds float. None on parse failure — caller skips that row.

    Without this, a naive datetime's .timestamp() uses the local server TZ —
    silent off-by-hours errors crossing window boundaries (90-day velocity).
    """
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# Scorecard brand → business_key in tools/twenty.py mapping.
# (twenty.py uses {callingdigital, autointelligence, bookd}; the scorecards
# use the shorter brand slugs.)
BRAND_TO_BUSINESS_KEY = {
    "wd":   "callingdigital",     # Worship Digital = post-rebrand CD per memory
    "avi":  "autointelligence",
    "aipg": None,                  # AIPG still on GHL per default business_crm_map
    "bookd": "bookd",
}


def fetch(kpi_spec: dict, run_ctx) -> List[KPIReading]:
    name = kpi_spec.get("name") or ""
    brands = kpi_spec.get("per_brand") or []
    if not brands:
        raise ValueError(f"twenty_opportunity_log: kpi {name!r} has no per_brand list")

    readings: List[KPIReading] = []
    for brand in brands:
        biz_key = BRAND_TO_BUSINESS_KEY.get(brand)
        if biz_key is None:
            readings.append(KPIReading(
                persona="cro", kpi_name=name, brand=brand,
                status="no_data",
                error_detail=f"brand {brand!r} not mapped to a Twenty workspace",
            ))
            continue
        if not twenty_ready(biz_key):
            readings.append(KPIReading(
                persona="cro", kpi_name=name, brand=brand,
                status="connector_down",
                error_detail=f"Twenty workspace for {biz_key!r} not configured",
            ))
            continue
        try:
            if name == "new_opportunities_per_week":
                readings.append(_new_opportunities_per_week(brand, biz_key))
            elif name == "pipeline_coverage_ratio":
                readings.append(_pipeline_coverage_ratio(brand, biz_key, kpi_spec))
            elif name == "pipeline_velocity_days":
                readings.append(_pipeline_velocity_days(brand, biz_key))
            else:
                raise ValueError(f"twenty_opportunity_log: unsupported kpi {name!r}")
        except requests.HTTPError as e:
            readings.append(KPIReading(
                persona="cro", kpi_name=name, brand=brand,
                status="connector_down", error_detail=str(e)[:500],
            ))
    return readings


def _list_opportunities(biz_key: str, limit: int = 200) -> list:
    """Pull a recent slice of opportunities. Twenty doesn't yet expose
    server-side date filters via simple REST, so we pull the most recent N
    and post-filter. For a single-brand workspace this fits comfortably under
    one request for early-stage pipelines.
    """
    base_url, api_key = _workspace_config(biz_key)
    r = requests.get(
        f"{base_url}/rest/opportunities",
        headers=_headers(api_key),
        params={"limit": limit, "order_by": "createdAt[DescNullsLast]"},
        timeout=_REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    return (r.json().get("data") or {}).get("opportunities") or []


def _new_opportunities_per_week(brand: str, biz_key: str) -> KPIReading:
    opps = _list_opportunities(biz_key)
    cutoff = datetime.now(timezone.utc).timestamp() - 7 * 86400
    count = 0
    for o in opps:
        ts = _parse_twenty_ts(o.get("createdAt") or "")
        if ts is not None and ts >= cutoff:
            count += 1
    return KPIReading(
        persona="cro",
        kpi_name="new_opportunities_per_week",
        brand=brand,
        value_numeric=float(count),
        unit="opportunities/week",
        raw_payload={"window_days": 7, "sampled_opps": len(opps)},
    )


def _pipeline_coverage_ratio(brand: str, biz_key: str, kpi_spec: dict) -> KPIReading:
    """Coverage ratio = sum(open opportunity amount) / remaining quarterly target.

    Quarterly target lives in kpi_spec extension `quarterly_target_per_brand`
    once Michael locks Phase A targets. Until then we return what we CAN compute
    (open-pipeline value) and leave the ratio in raw_payload as TBD.
    """
    opps = _list_opportunities(biz_key)
    open_value = 0.0
    open_count = 0
    for o in opps:
        stage = (o.get("stage") or "").lower()
        if stage in {"won", "closed_won", "lost", "closed_lost"}:
            continue
        amount = (o.get("amount") or {})
        if isinstance(amount, dict):
            # Twenty stores currency amounts as {amountMicros, currencyCode}
            micros = amount.get("amountMicros") or 0
            try:
                open_value += float(micros) / 1_000_000.0
            except Exception:
                pass
        open_count += 1

    target = (kpi_spec.get("quarterly_target_per_brand") or {}).get(brand)
    if not target:
        return KPIReading(
            persona="cro", kpi_name="pipeline_coverage_ratio", brand=brand,
            value_numeric=None, unit="x", status="no_data",
            error_detail="quarterly_target_per_brand not yet set in scorecard",
            raw_payload={"open_value_usd": round(open_value, 2), "open_count": open_count},
        )

    ratio = round(open_value / float(target), 3) if target else 0.0
    return KPIReading(
        persona="cro", kpi_name="pipeline_coverage_ratio", brand=brand,
        value_numeric=ratio, unit="x",
        raw_payload={
            "open_value_usd": round(open_value, 2),
            "open_count": open_count,
            "quarterly_target_usd": float(target),
        },
    )


def _pipeline_velocity_days(brand: str, biz_key: str) -> KPIReading:
    """Median days from createdAt to closeDate on opportunities closed-won
    in the last 90 days. Twenty exposes both fields on the opportunity object."""
    opps = _list_opportunities(biz_key, limit=500)
    cutoff = datetime.now(timezone.utc).timestamp() - 90 * 86400
    spans_days: List[float] = []
    for o in opps:
        stage = (o.get("stage") or "").lower()
        if stage not in {"won", "closed_won"}:
            continue
        close_ts = _parse_twenty_ts(o.get("closeDate") or "")
        create_ts = _parse_twenty_ts(o.get("createdAt") or "")
        if close_ts is None or create_ts is None:
            continue
        if close_ts < cutoff:
            continue
        spans_days.append((close_ts - create_ts) / 86400.0)

    if not spans_days:
        return KPIReading(
            persona="cro", kpi_name="pipeline_velocity_days", brand=brand,
            status="no_data",
            raw_payload={"closed_won_in_window": 0, "window_days": 90},
        )
    med = round(median(spans_days), 1)
    return KPIReading(
        persona="cro", kpi_name="pipeline_velocity_days", brand=brand,
        value_numeric=med, unit="days (median)",
        raw_payload={"closed_won_in_window": len(spans_days), "window_days": 90},
    )
