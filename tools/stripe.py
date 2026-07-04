"""tools/stripe.py — Stripe get-paid rail helpers (GUARDED).

Payee entity for the Stripe account: Automotive Intelligence LLC.

Everything here is additive and degrades cleanly when STRIPE_SECRET_KEY is
absent (same discipline as the Twilio wiring): stripe_ready() returns False,
and the provisioning helpers raise a single explicit RuntimeError that callers
(scripts/stripe_setup.py) surface as a skip, never a crash. The `stripe` SDK is
imported lazily so merely importing this module never fails even if the SDK is
not yet installed.

Reads the approved catalog from config/pricing.yaml via config.pricing, so the
Products/Prices/Payment Links we create always match quoted (SOW) pricing.

Idempotency: Prices are anchored by a stable `lookup_key` derived from the offer
key, so re-running provisioning reuses the existing Price instead of minting a
duplicate. Products are matched by metadata `catalog_key`.

CLIENT-FACING STRINGS carry no em-dashes (repo rule).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from config import pricing

logger = logging.getLogger(__name__)

_API_VERSION = "2024-06-20"


def stripe_secret_key() -> Optional[str]:
    """The Stripe secret key from env, or None. Never logs the value."""
    return (os.getenv("STRIPE_SECRET_KEY") or "").strip() or None


def stripe_ready() -> bool:
    """True iff a Stripe secret key is present AND the SDK importable.

    Callers gate on this before any live Stripe call so the rail no-ops
    cleanly pre-activation.
    """
    if stripe_secret_key() is None:
        return False
    try:
        import stripe  # noqa: F401
    except ImportError:
        logger.warning("[stripe] STRIPE_SECRET_KEY present but `stripe` SDK not installed")
        return False
    return True


def _client():
    """Return the configured `stripe` module, or raise a single explicit error.

    Guard boundary: every live helper funnels through here, so the 'missing
    creds' failure mode is one clear message rather than an opaque SDK stack.
    """
    key = stripe_secret_key()
    if key is None:
        raise RuntimeError(
            "STRIPE_SECRET_KEY is not set. The Stripe rail is inactive. "
            "Add the key to Doppler paperclip/prd to activate."
        )
    try:
        import stripe
    except ImportError as e:
        raise RuntimeError(
            "The `stripe` SDK is not installed. Run: pip install stripe"
        ) from e
    stripe.api_key = key
    stripe.api_version = _API_VERSION
    return stripe


def _cents(amount_usd) -> int:
    """Whole-dollar catalog amount to integer cents."""
    return int(round(float(amount_usd) * 100))


def _lookup_key(offer_key: str, kind: str) -> str:
    """Stable, unique Price lookup_key. kind ∈ {'recurring','setup','onetime'}."""
    return f"{offer_key}__{kind}"


# ── Idempotent provisioning ────────────────────────────────────────────────


def ensure_product(offer: dict):
    """Find or create the Stripe Product for an offer (matched by metadata
    catalog_key). Returns the Product object. Requires live creds.
    """
    stripe = _client()
    catalog_key = offer["key"]
    existing = stripe.Product.search(query=f'metadata["catalog_key"]:"{catalog_key}"')
    if existing.data:
        return existing.data[0]
    return stripe.Product.create(
        name=offer["name"],
        description=offer.get("description") or None,
        metadata={"catalog_key": catalog_key, "brand": offer.get("brand", "")},
    )


def _ensure_price(product_id: str, offer_key: str, kind: str, amount_usd,
                  recurring_interval: Optional[str]):
    """Find (by lookup_key) or create a Price under a Product. Idempotent."""
    stripe = _client()
    lk = _lookup_key(offer_key, kind)
    found = stripe.Price.search(query=f'lookup_key:"{lk}"')
    if found.data:
        return found.data[0]
    kwargs = dict(
        product=product_id,
        currency=pricing.currency(),
        unit_amount=_cents(amount_usd),
        lookup_key=lk,
        metadata={"catalog_key": offer_key, "kind": kind},
    )
    if recurring_interval:
        kwargs["recurring"] = {"interval": recurring_interval}
    return stripe.Price.create(**kwargs)


def ensure_prices(offer: dict, product_id: str) -> dict:
    """Ensure all Prices an offer needs exist. Returns {kind: price_obj}.

    - recurring offer  -> 'recurring' (monthly) [+ 'setup' one-time if setup_usd]
    - one_time offer   -> 'onetime'
    """
    prices: dict = {}
    otype = offer.get("type")
    if otype == "recurring":
        prices["recurring"] = _ensure_price(
            product_id, offer["key"], "recurring", offer["amount_usd"],
            offer.get("interval", "month"),
        )
        if offer.get("setup_usd"):
            prices["setup"] = _ensure_price(
                product_id, offer["key"], "setup", offer["setup_usd"], None,
            )
    elif otype == "one_time":
        prices["onetime"] = _ensure_price(
            product_id, offer["key"], "onetime", offer["amount_usd"], None,
        )
    else:
        raise ValueError(f"offer {offer['key']!r} has unknown type {otype!r}")
    return prices


def ensure_payment_link(offer: dict, prices: dict):
    """Find or create a recurring Payment Link for an offer. Idempotent via a
    metadata match on catalog_key. Returns the PaymentLink object (has `.url`).

    A setup fee (when present) is added as a second line item so the first
    charge collects setup + first month in one link.
    """
    stripe = _client()
    catalog_key = offer["key"]

    # Reuse an existing active link for this offer if one exists.
    for link in stripe.PaymentLink.list(limit=100, active=True).auto_paging_iter():
        if (link.metadata or {}).get("catalog_key") == catalog_key:
            return link

    line_items = []
    if offer.get("type") == "recurring":
        line_items.append({"price": prices["recurring"].id, "quantity": 1})
        if "setup" in prices:
            line_items.append({"price": prices["setup"].id, "quantity": 1})
    else:
        line_items.append({"price": prices["onetime"].id, "quantity": 1})

    return stripe.PaymentLink.create(
        line_items=line_items,
        metadata={"catalog_key": catalog_key, "brand": offer.get("brand", "")},
    )


def provision_offer(offer: dict) -> dict:
    """End to end for one offer: Product -> Prices -> Payment Link.
    Idempotent. Returns a summary dict with the link URL.
    """
    product = ensure_product(offer)
    prices = ensure_prices(offer, product.id)
    link = ensure_payment_link(offer, prices)
    return {
        "key": offer["key"],
        "brand": offer.get("brand"),
        "name": offer["name"],
        "product_id": product.id,
        "payment_link_url": link.url,
        "price_ids": {k: v.id for k, v in prices.items()},
    }


# ── MRR read (used by the stripe_arr metric connector) ─────────────────────


def active_subscription_mrr() -> float:
    """Sum the monthly-normalized amount of all active/trialing subscriptions.

    Yearly intervals are divided by 12. Requires live creds (caller gates on
    stripe_ready()). Returns whole-dollar float.
    """
    stripe = _client()
    total_cents = 0
    subs = stripe.Subscription.list(status="active", limit=100, expand=["data.items"])
    for sub in subs.auto_paging_iter():
        for item in sub["items"]["data"]:
            price = item.get("price") or {}
            recurring = price.get("recurring") or {}
            unit = price.get("unit_amount") or 0
            qty = item.get("quantity") or 1
            interval = recurring.get("interval")
            count = recurring.get("interval_count") or 1
            monthly = unit * qty
            if interval == "year":
                monthly = monthly / (12 * count)
            elif interval == "week":
                monthly = monthly * 52 / 12 / count
            elif interval == "month":
                monthly = monthly / count
            total_cents += monthly
    return round(total_cents / 100.0, 2)
