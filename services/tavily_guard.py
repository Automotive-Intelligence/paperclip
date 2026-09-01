"""services/tavily_guard.py -- the one hard ceiling on Tavily spend.

2026-08-31 (Michael, Sales Desk 30-day accountability review): paygo_limit
is null (genuinely uncapped) and August closed at 4,920/4,000 credits --
$7.76 billed straight through with nothing metering it, on top of a month
of Tyler/Marcus/Ryan Data prospecting that delivered 287 real prospects
total (mostly discarded to hallucinated phone numbers/names despite
explicit anti-fabrication instructions). "Cap Tavily today regardless of
the above. No ceiling is not an option."

This is the enforcement gate every Tavily caller passes through BEFORE
making a real API call -- tools/web_search.py (the CrewAI tool) and
tools/contact_enricher.py (post-parse enrichment) both call check_budget()
first and skip the real call on a block. Fail CLOSED: an unreadable usage
check blocks the call rather than risk an unmetered spend -- the same
"meter reads zero" trap that let August's overage go unnoticed.

This is a defense-in-depth CODE-level ceiling, not a substitute for the
account-level cap: Tavily's own paygo limit is dashboard-only (no documented
API to set it) -- https://app.tavily.com/billing, "Pay as You Go" section,
toggle the limit on, set a monthly amount.

TAVILY_HARD_CAP_CREDITS is a real, conservative interim ceiling (well below
the 4,000/mo plan_limit), not a target -- pending the Sales Desk fix-or-pause
decision. Override via env if that decision raises it.
"""
from __future__ import annotations

import logging
import os
from typing import Tuple

logger = logging.getLogger(__name__)

DEFAULT_HARD_CAP = 500  # credits/month -- interim ceiling, see module docstring


def hard_cap() -> int:
    raw = (os.environ.get("TAVILY_HARD_CAP_CREDITS") or "").strip()
    try:
        return int(raw) if raw else DEFAULT_HARD_CAP
    except ValueError:
        return DEFAULT_HARD_CAP


def check_budget() -> Tuple[bool, str]:
    """(allowed, reason). Fail CLOSED: any read failure blocks the call.

    Uses plan_usage as the authoritative this-cycle count -- Tavily mirrors
    paygo_usage to the same running total while under plan_limit (verified
    live 2026-08-31: both read 28 on a fresh cycle), so plan_usage is the
    honest total rather than double-counting via plan_usage + paygo_usage.
    """
    try:
        from services.llm_ledger import tavily_usage
        u = tavily_usage()
    except Exception as e:
        logger.warning("[tavily-guard] usage check failed, blocking: %s", e)
        return False, f"usage_check_failed:{type(e).__name__}"
    if u is None:
        logger.warning("[tavily-guard] usage unreadable, blocking")
        return False, "usage_unreadable"

    cap = hard_cap()
    used = int(u.get("plan_usage") or 0)
    if used >= cap:
        logger.warning("[tavily-guard] BLOCKED: %d/%d credits used this cycle", used, cap)
        return False, f"cap_reached:{used}/{cap}"
    return True, f"ok:{used}/{cap}"
