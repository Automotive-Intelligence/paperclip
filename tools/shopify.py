"""tools/shopify.py — Shopify Admin API read tools for Nova + Carlos.

Wraps Shopify Admin REST API (2024-10) so Nova can audit a tenant's storefront
and Carlos can pull metrics for the bi-weekly performance reports. Read-only
in Phase 1: no order mutations, no product writes, no theme edits.

Per-tenant credential resolution (matches tools/outbound_email pattern):
  SHOPIFY_SHOP_<BUSINESSKEY>           e.g. paperandpurpose (no .myshopify.com)
  SHOPIFY_ADMIN_TOKEN_<BUSINESSKEY>    private app / custom app token

For Calling Digital agency setup: each client business_key gets its own token.
The CD agency Klaviyo / Shopify partner status lives outside this module.

Tools exposed:
  - audit_shopify_storefront(business_key) — Nova's structured audit
  - get_storefront_metrics(business_key, days=14) — orders + revenue + AOV
  - get_top_products(business_key, days=14) — best sellers by units sold
  - get_recent_orders(business_key, limit=10) — most recent orders, lightweight

Each tool returns a JSON-stringified result Marcus/Carlos/Nova can reason over.
Errors return as strings (not raises) — same pattern as web_search_tool /
tools/keyapi.py — so the LLM gets useful feedback instead of a crashed Crew.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
from collections import defaultdict
from typing import Any

import requests
from crewai.tools import tool

logger = logging.getLogger(__name__)

DEFAULT_API_VERSION = "2024-10"
DEFAULT_TIMEOUT = 30


def _suffix(business_key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (business_key or "").strip().lower()).upper()


def _shop_for(business_key: str) -> str | None:
    """Return the shop's myshopify subdomain (e.g. 'paperandpurpose')."""
    suffix = _suffix(business_key)
    if not suffix:
        return None
    val = (os.environ.get(f"SHOPIFY_SHOP_{suffix}") or "").strip()
    return val or None


def _token_for(business_key: str) -> str | None:
    suffix = _suffix(business_key)
    if not suffix:
        return None
    val = (os.environ.get(f"SHOPIFY_ADMIN_TOKEN_{suffix}") or "").strip()
    return val or None


def _api_version() -> str:
    raw = (os.environ.get("SHOPIFY_API_VERSION") or "").strip()
    return raw or DEFAULT_API_VERSION


def _shopify_get(business_key: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | str:
    """Low-level GET to <shop>.myshopify.com/admin/api/<ver>/<path>.

    Returns parsed JSON dict on success, or a human-readable error string on
    failure (suitable for returning to the LLM). Never raises.
    """
    shop = _shop_for(business_key)
    token = _token_for(business_key)
    if not shop:
        return f"ERROR: SHOPIFY_SHOP_{_suffix(business_key)} env var not set."
    if not token:
        return f"ERROR: SHOPIFY_ADMIN_TOKEN_{_suffix(business_key)} env var not set."

    base = f"https://{shop}.myshopify.com/admin/api/{_api_version()}"
    url = f"{base}/{path.lstrip('/')}"
    headers = {
        "X-Shopify-Access-Token": token,
        "Accept": "application/json",
    }

    try:
        resp = requests.get(url, headers=headers, params=params or {}, timeout=DEFAULT_TIMEOUT)
    except requests.exceptions.Timeout:
        return f"ERROR: Shopify timeout on {path} (>{DEFAULT_TIMEOUT}s)"
    except requests.exceptions.RequestException as e:
        return f"ERROR: Shopify request failed on {path}: {type(e).__name__}: {e}"

    if resp.status_code == 401:
        return "ERROR: Shopify rejected the admin token (401). Verify SHOPIFY_ADMIN_TOKEN_* and that the app has the required scopes."
    if resp.status_code == 403:
        return "ERROR: Shopify forbade access (403). The token's scopes don't cover this resource."
    if resp.status_code == 404:
        return f"ERROR: Shopify 404 on {path}: shop '{shop}' or resource may not exist."
    if resp.status_code == 429:
        return "ERROR: Shopify rate limit hit (429). Slow down or wait a few seconds."
    if resp.status_code >= 400:
        return f"ERROR: Shopify HTTP {resp.status_code} on {path}: {resp.text[:300]}"

    try:
        return resp.json()
    except ValueError:
        return f"ERROR: Shopify returned non-JSON on {path}: {resp.text[:300]}"


def _truncate_json(obj: Any, max_chars: int = 8000) -> str:
    out = json.dumps(obj, indent=2, default=str)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n\n[...truncated for context budget...]"
    return out


def _iso_days_ago(days: int) -> str:
    n = max(1, int(days))
    return (datetime.datetime.utcnow() - datetime.timedelta(days=n)).replace(microsecond=0).isoformat() + "Z"


# ---------------------------------------------------------------------------
# Nova: storefront audit
# ---------------------------------------------------------------------------

@tool("Audit Shopify Storefront")
def audit_shopify_storefront(business_key: str) -> str:
    """Diagnostic audit of a tenant's Shopify storefront.

    Returns shop metadata (name, currency, timezone, plan), product/collection
    counts, primary checkout flow, and high-signal config gaps Nova should
    surface in the audit deliverable for the campaign.

    Args:
        business_key: Tenant key (e.g. 'paper_and_purpose'). Per-tenant Shopify
            credentials are resolved from SHOPIFY_SHOP_<KEY> + SHOPIFY_ADMIN_TOKEN_<KEY>.

    Returns: Audit summary as JSON string, or an error message.
    """
    business_key = (business_key or "").strip()
    if not business_key:
        return "ERROR: business_key is required."

    shop_resp = _shopify_get(business_key, "shop.json")
    if isinstance(shop_resp, str):
        return shop_resp

    products_resp = _shopify_get(business_key, "products/count.json")
    collections_resp = _shopify_get(business_key, "custom_collections/count.json")
    smart_collections_resp = _shopify_get(business_key, "smart_collections/count.json")

    shop = shop_resp.get("shop") or {}
    audit = {
        "business_key": business_key,
        "shop": {
            "name": shop.get("name"),
            "domain": shop.get("domain"),
            "myshopify_domain": shop.get("myshopify_domain"),
            "email": shop.get("email"),
            "currency": shop.get("currency"),
            "iana_timezone": shop.get("iana_timezone"),
            "country_code": shop.get("country_code"),
            "plan_name": shop.get("plan_name"),
            "plan_display_name": shop.get("plan_display_name"),
            "checkout_api_supported": shop.get("checkout_api_supported"),
            "primary_locale": shop.get("primary_locale"),
            "created_at": shop.get("created_at"),
            "shop_owner": shop.get("shop_owner"),
        },
        "counts": {
            "products": (products_resp.get("count") if isinstance(products_resp, dict) else f"err: {products_resp}"),
            "custom_collections": (collections_resp.get("count") if isinstance(collections_resp, dict) else f"err: {collections_resp}"),
            "smart_collections": (smart_collections_resp.get("count") if isinstance(smart_collections_resp, dict) else f"err: {smart_collections_resp}"),
        },
        "audit_signals": _generate_audit_signals(shop, products_resp, collections_resp, smart_collections_resp),
    }
    return _truncate_json(audit)


def _generate_audit_signals(
    shop: dict[str, Any],
    products_resp: Any,
    collections_resp: Any,
    smart_collections_resp: Any,
) -> list[str]:
    """Surface high-signal gaps Nova should call out. Plain strings — Nova
    expands these into the audit narrative."""
    signals: list[str] = []
    if not shop.get("checkout_api_supported"):
        signals.append("Checkout API not supported on this plan — limits headless/custom checkout work.")
    if shop.get("plan_name") in ("partner_test", "trial"):
        signals.append(f"Shop is on a {shop.get('plan_name')!r} plan — pre-launch only; will need real plan before the launch.")
    p_count = products_resp.get("count") if isinstance(products_resp, dict) else None
    if isinstance(p_count, int) and p_count == 0:
        signals.append("Zero products in catalog. Cannot launch until SKUs are added.")
    elif isinstance(p_count, int) and p_count == 1:
        signals.append("Single-product catalog. Confirm reward tiers / variants are modeled correctly.")
    c_count_a = collections_resp.get("count") if isinstance(collections_resp, dict) else None
    c_count_b = smart_collections_resp.get("count") if isinstance(smart_collections_resp, dict) else None
    total_collections = (c_count_a or 0) + (c_count_b or 0) if (c_count_a is not None and c_count_b is not None) else None
    if total_collections == 0:
        signals.append("No collections configured. Add at least one collection so the storefront has navigable structure.")
    if shop.get("primary_locale") and not str(shop["primary_locale"]).startswith("en"):
        signals.append(f"Primary locale is {shop['primary_locale']!r}. Confirm locale matches the launch audience.")
    return signals


# ---------------------------------------------------------------------------
# Carlos: storefront metrics for the 14-day report
# ---------------------------------------------------------------------------

@tool("Get Shopify Storefront Metrics")
def get_storefront_metrics(business_key: str, days: int = 14) -> str:
    """Return order count, gross sales, and AOV for the past N days.

    Use this to populate Carlos's bi-weekly performance reports. Currency is
    whatever the shop is configured in (USD for most CD clients).

    Args:
        business_key: Tenant key.
        days: Window length in days. Default 14, max 90.

    Returns: Metrics summary as JSON string, or an error message.
    """
    n = max(1, min(int(days) if days else 14, 90))
    since = _iso_days_ago(n)
    resp = _shopify_get(
        business_key,
        "orders.json",
        {
            "status": "any",
            "created_at_min": since,
            "limit": 250,
            "fields": "id,created_at,total_price,subtotal_price,total_tax,currency,financial_status",
        },
    )
    if isinstance(resp, str):
        return resp

    orders = resp.get("orders") or []
    paid = [o for o in orders if o.get("financial_status") in ("paid", "partially_paid", "authorized")]
    gross = sum(float(o.get("total_price") or 0) for o in paid)
    subtotal = sum(float(o.get("subtotal_price") or 0) for o in paid)
    tax = sum(float(o.get("total_tax") or 0) for o in paid)
    n_paid = len(paid)
    aov = round(gross / n_paid, 2) if n_paid else 0.0
    currency = paid[0].get("currency") if paid else (orders[0].get("currency") if orders else None)

    summary = {
        "business_key": business_key,
        "window_days": n,
        "since_utc": since,
        "currency": currency,
        "orders_total_in_window": len(orders),
        "orders_paid_in_window": n_paid,
        "gross_sales": round(gross, 2),
        "subtotal_sales": round(subtotal, 2),
        "tax_collected": round(tax, 2),
        "average_order_value": aov,
    }
    return _truncate_json(summary)


@tool("Get Top Shopify Products")
def get_top_products(business_key: str, days: int = 14, top_n: int = 5) -> str:
    """Return the best-selling products in the past N days, ranked by units sold.

    Aggregates from orders.json line items. For storefronts with low order volume
    (typical pre-launch / launch week), this gives a directionally useful read on
    which SKUs are converting.

    Args:
        business_key: Tenant key.
        days: Window length. Default 14, max 90.
        top_n: How many top products to return. Default 5, max 25.

    Returns: Ranked list of products as JSON string, or an error message.
    """
    n = max(1, min(int(days) if days else 14, 90))
    k = max(1, min(int(top_n) if top_n else 5, 25))
    since = _iso_days_ago(n)
    resp = _shopify_get(
        business_key,
        "orders.json",
        {
            "status": "any",
            "created_at_min": since,
            "limit": 250,
            "fields": "id,created_at,line_items,financial_status,currency",
        },
    )
    if isinstance(resp, str):
        return resp

    orders = resp.get("orders") or []
    counter: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"product_id": None, "title": None, "units": 0, "revenue": 0.0}
    )
    for o in orders:
        if o.get("financial_status") not in ("paid", "partially_paid", "authorized"):
            continue
        for li in o.get("line_items") or []:
            pid = str(li.get("product_id") or "")
            title = li.get("title") or "(untitled)"
            qty = int(li.get("quantity") or 0)
            rev = float(li.get("price") or 0) * qty
            key = (pid, title)
            counter[key]["product_id"] = pid
            counter[key]["title"] = title
            counter[key]["units"] += qty
            counter[key]["revenue"] = round(counter[key]["revenue"] + rev, 2)

    ranked = sorted(counter.values(), key=lambda x: (x["units"], x["revenue"]), reverse=True)[:k]
    return _truncate_json({
        "business_key": business_key,
        "window_days": n,
        "since_utc": since,
        "top_products": ranked,
    })


@tool("Get Recent Shopify Orders")
def get_recent_orders(business_key: str, limit: int = 10) -> str:
    """Return the most recent N orders with lightweight fields suitable for
    spot-checking conversion patterns. Excludes line items and customer PII.

    Args:
        business_key: Tenant key.
        limit: Number of orders. Default 10, capped at 50.

    Returns: Recent orders as JSON string, or an error message.
    """
    k = max(1, min(int(limit) if limit else 10, 50))
    resp = _shopify_get(
        business_key,
        "orders.json",
        {
            "status": "any",
            "limit": k,
            "fields": "id,name,created_at,financial_status,fulfillment_status,total_price,currency,source_name",
        },
    )
    if isinstance(resp, str):
        return resp
    return _truncate_json({
        "business_key": business_key,
        "orders": resp.get("orders") or [],
    })


# ---------------------------------------------------------------------------
# Status / observability
# ---------------------------------------------------------------------------

def shopify_status() -> dict[str, Any]:
    """Lightweight observability — used by /admin endpoints if needed.
    Reports which business_keys have both a shop AND a token configured."""
    configured: dict[str, dict[str, bool]] = {}
    for k in os.environ:
        if k.startswith("SHOPIFY_SHOP_"):
            suffix = k.removeprefix("SHOPIFY_SHOP_")
            configured.setdefault(suffix, {})["shop_set"] = True
        if k.startswith("SHOPIFY_ADMIN_TOKEN_"):
            suffix = k.removeprefix("SHOPIFY_ADMIN_TOKEN_")
            configured.setdefault(suffix, {})["token_set"] = True
    for d in configured.values():
        d["ready"] = bool(d.get("shop_set") and d.get("token_set"))
    return {
        "api_version": _api_version(),
        "tenants": configured,
    }


SHOPIFY_TOOLS = [
    audit_shopify_storefront,
    get_storefront_metrics,
    get_top_products,
    get_recent_orders,
]
