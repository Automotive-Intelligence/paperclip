"""config/pricing.py — Loader for the approved product catalog (pricing.yaml).

Single accessor so tools/stripe.py, scripts/stripe_setup.py, the stripe_arr
metric connector, and proposal generation all read the SAME six offers. Parsing
lives here (not duplicated per consumer) and is cached after first read.

Owner-approved 2026-07-04. Payee entity: Automotive Intelligence LLC.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import List, Optional

import yaml

_PRICING_PATH = os.path.join(os.path.dirname(__file__), "pricing.yaml")


@lru_cache(maxsize=1)
def _load() -> dict:
    with open(_PRICING_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def currency() -> str:
    return (_load().get("currency") or "usd").lower()


def payee_entity() -> str:
    return _load().get("payee_entity") or ""


def all_offers() -> List[dict]:
    """Every approved offer, in catalog order. Each dict carries at least:
    key, brand, name, description, type ('one_time'|'recurring'), amount_usd.
    Recurring offers also carry `interval` and may carry `setup_usd`,
    `min_term_months`.
    """
    return list(_load().get("offers") or [])


def offer(key: str) -> Optional[dict]:
    """One offer by its stable `key`, or None."""
    for o in all_offers():
        if o.get("key") == key:
            return o
    return None


def recurring_offers() -> List[dict]:
    return [o for o in all_offers() if (o.get("type") or "") == "recurring"]
