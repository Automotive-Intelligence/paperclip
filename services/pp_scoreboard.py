"""
services/pp_scoreboard.py -- Paper & Purpose launch scoreboard data layer.

Per Pit Wall flag #4 (2026-06-15). Miriam is targeting 250 pre-orders by
2026-07-01; this endpoint gives Michael + Miriam + the CMG a live signup +
order rollup surface so nobody has to eyeball two dashboards.

Sources:
  * Klaviyo    -- signup count in the last N days (email captures)
  * Shopify    -- order count + units sold + revenue in the last N days

Consumed by:
  * GET /admin/scoreboard/pp -- JSON for cockpit + ad-hoc curl
  * P&P daily brief (future) -- shape is stable enough to embed verbatim

Graceful degradation: if either env var is missing, the affected metrics
come back as None with an explicit `notes` explaining why, mirroring how
services/aipg_scoreboard.py handles missing GHL tokens. So the endpoint is
useful even before all tokens land -- it becomes MORE useful as tokens
are provisioned.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


_REQUEST_TIMEOUT = 12


# ---------------------------------------------------------------------------
# Klaviyo -- signup count
# ---------------------------------------------------------------------------


_KLAVIYO_BASE = "https://a.klaviyo.com/api"
_KLAVIYO_REVISION = "2024-10-15"


def _klaviyo_headers() -> Optional[Dict[str, str]]:
    token = (os.getenv("KLAVIYO_API_KEY") or "").strip()
    if not token:
        return None
    return {
        "Authorization": f"Klaviyo-API-Key {token}",
        "Accept": "application/json",
        "revision": _KLAVIYO_REVISION,
    }


def _klaviyo_count_signups(days: int) -> Tuple[Optional[int], Optional[str]]:
    """Count Klaviyo profiles created in the trailing N days.

    Returns (count, blocker_reason). count is None when unreadable.
    """
    headers = _klaviyo_headers()
    if not headers:
        return None, "KLAVIYO_API_KEY missing (Doppler paperclip/prd)"
    since = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)).isoformat()
    total = 0
    cursor: Optional[str] = None
    pages = 0
    try:
        while True:
            pages += 1
            if pages > 50:  # safety cap
                break
            params = {"filter": f"greater-than(created,{since})", "page[size]": 100}
            if cursor:
                params["page[cursor]"] = cursor
            r = requests.get(
                f"{_KLAVIYO_BASE}/profiles/",
                headers=headers, params=params, timeout=_REQUEST_TIMEOUT,
            )
            if not r.ok:
                return None, f"Klaviyo /profiles/ HTTP {r.status_code}: {r.text[:120]}"
            body = r.json()
            data = body.get("data") or []
            total += len(data)
            next_url = ((body.get("links") or {}).get("next") or "")
            if not next_url:
                break
            # Klaviyo returns full next-page URL; extract page[cursor].
            if "page[cursor]=" in next_url:
                cursor = next_url.split("page[cursor]=", 1)[1].split("&", 1)[0]
            else:
                break
    except requests.RequestException as e:
        return None, f"Klaviyo network: {type(e).__name__}"
    return total, None


# ---------------------------------------------------------------------------
# Shopify -- order + units + revenue
# ---------------------------------------------------------------------------


_SHOPIFY_API_VERSION = "2024-10"


def _shopify_headers_and_url(store: str) -> Optional[Tuple[Dict[str, str], str]]:
    token = (os.getenv("SHOPIFY_PP_TOKEN") or "").strip()
    if not token or not store:
        return None
    base = f"https://{store}.myshopify.com/admin/api/{_SHOPIFY_API_VERSION}"
    return {
        "X-Shopify-Access-Token": token,
        "Accept": "application/json",
    }, base


def _shopify_count_orders(days: int) -> Tuple[
    Optional[int], Optional[int], Optional[float], Optional[str]
]:
    """Count Shopify orders + units + revenue for the trailing N days.

    Returns (order_count, unit_count, revenue_usd, blocker_reason). Any of the
    first three can be None if unreachable.
    """
    store = (os.getenv("SHOPIFY_PP_STORE") or "nsapaq-qu").strip()
    hu = _shopify_headers_and_url(store)
    if not hu:
        return None, None, None, "SHOPIFY_PP_TOKEN missing (Doppler paperclip/prd)"
    headers, base = hu

    since = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)).isoformat()
    orders_seen = 0
    units_seen = 0
    revenue = 0.0

    # Shopify paginates via Link header; step via page_info tokens.
    next_url: Optional[str] = f"{base}/orders.json"
    params: Dict[str, Any] = {
        "created_at_min": since,
        "status": "any",
        "financial_status": "paid,partially_paid,pending",
        "limit": 250,
        "fields": "id,line_items,total_price,currency,cancelled_at",
    }
    pages = 0
    try:
        while next_url:
            pages += 1
            if pages > 40:
                break
            r = requests.get(next_url, headers=headers, params=params, timeout=_REQUEST_TIMEOUT)
            if not r.ok:
                return None, None, None, f"Shopify /orders.json HTTP {r.status_code}: {r.text[:120]}"
            body = r.json()
            for order in body.get("orders") or []:
                if order.get("cancelled_at"):
                    continue
                orders_seen += 1
                for li in order.get("line_items") or []:
                    units_seen += int(li.get("quantity") or 0)
                try:
                    revenue += float(order.get("total_price") or 0)
                except (TypeError, ValueError):
                    pass
            # Handle Shopify Link-header cursor pagination.
            link = r.headers.get("Link") or r.headers.get("link") or ""
            next_url = None
            params = {}
            for part in link.split(","):
                part = part.strip()
                if 'rel="next"' in part and "<" in part and ">" in part:
                    next_url = part[part.find("<") + 1:part.find(">")]
                    break
    except requests.RequestException as e:
        return None, None, None, f"Shopify network: {type(e).__name__}"

    return orders_seen, units_seen, round(revenue, 2), None


# ---------------------------------------------------------------------------
# Rollup dataclass
# ---------------------------------------------------------------------------


@dataclass
class PpScoreboard:
    window_days: int
    signups_count: Optional[int]
    orders_count: Optional[int]
    units_count: Optional[int]
    revenue_usd: Optional[float]
    pre_order_goal: int = 250
    pre_order_deadline: str = "2026-07-01"
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        progress_pct: Optional[float] = None
        if self.units_count is not None and self.pre_order_goal:
            progress_pct = round((self.units_count / self.pre_order_goal) * 100, 1)
        return {
            "window_days": self.window_days,
            "klaviyo": {"signups": self.signups_count},
            "shopify": {
                "orders": self.orders_count,
                "units": self.units_count,
                "revenue_usd": self.revenue_usd,
            },
            "pre_order_goal": {
                "target_units": self.pre_order_goal,
                "deadline": self.pre_order_deadline,
                "progress_pct": progress_pct,
            },
            "notes": self.notes,
        }


def build_pp_scoreboard(days: int = 7) -> PpScoreboard:
    """Live P&P launch scoreboard for the trailing `days` window."""
    signups, klaviyo_reason = _klaviyo_count_signups(days)
    orders, units, revenue, shopify_reason = _shopify_count_orders(days)

    notes: List[str] = []
    if signups is None:
        notes.append(f"signups_count unavailable — {klaviyo_reason}")
    if orders is None:
        notes.append(f"orders/units/revenue unavailable — {shopify_reason}")
    if orders is not None and orders == 0:
        notes.append(
            "0 orders in window — expected pre-launch (deadline 2026-07-01, "
            "goal 250 units). Watch daily as launch approaches."
        )
    return PpScoreboard(
        window_days=days,
        signups_count=signups,
        orders_count=orders,
        units_count=units,
        revenue_usd=revenue,
        notes=notes,
    )


def run_now_json(days: int = 7) -> Dict[str, Any]:
    """Manual-trigger endpoint response shape."""
    return build_pp_scoreboard(days).to_dict()


__all__ = ["PpScoreboard", "build_pp_scoreboard", "run_now_json"]
