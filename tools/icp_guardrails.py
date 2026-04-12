"""
tools/icp_guardrails.py - ICP validation for sales agent prospects.

Each sales agent has a defined Ideal Customer Profile. Prospects that
fall outside the ICP are discarded before CRM push, and the discard
reason is logged to PostgreSQL.
"""

import logging
import re
from typing import Dict, List, Optional, Tuple

from services.database import execute_query

logger = logging.getLogger(__name__)

# ── ICP Definitions ──────────────────────────────────────────────────────────

TYLER_ICP = {
    "agent": "tyler",
    "target_cities": [
        "aubrey", "celina", "prosper", "pilot point", "little elm",
    ],
    "target_industries": [
        "plumber", "plumbing", "hvac", "heating", "cooling", "air conditioning",
        "roofer", "roofing", "dental", "dentist", "dental office",
        "personal injury", "pi law", "law firm", "attorney",
    ],
    "max_employees": 10,
    # NOTE: 'live chat' and 'chatbot' removed — having a chat widget is a
    # buying signal (they care about response coverage but lack real 24/7).
    # Sophie replaces broken widgets, not competes with them.
    "exclude_keywords": [
        "franchise", "franchisee", "chain",
        "ai receptionist", "ai phone", "virtual receptionist",
    ],
}

MARCUS_ICP = {
    "agent": "marcus",
    "target_area": "texas",
    "target_verticals": [
        "med spa", "medspa", "medical spa", "aesthetics", "cosmetic",
        "personal injury", "pi law", "injury attorney", "injury lawyer",
        "real estate", "realtor", "brokerage", "realty",
        "custom home", "home builder", "homebuilder", "general contractor",
    ],
    "exclude_keywords": [
        "enterprise", "national chain", "fortune 500", "corporate",
        "ai receptionist", "ai phone", "virtual receptionist",
        "franchise", "franchisee",
    ],
}

RYAN_DATA_ICP = {
    "agent": "ryan_data",
    "target_area": "nationwide",
    "target_signals": [
        "ownership change", "new gm", "general manager", "declining reviews",
        "job posting", "bdc", "low response", "hiring",
    ],
    "exclude_keywords": [
        "ai tool", "ai phone", "virtual assistant", "chatbot",
        "buy here pay here", "bhph", "auction only", "auction-only",
        "wholesale only",
    ],
}

ICP_MAP = {
    "tyler": TYLER_ICP,
    "marcus": MARCUS_ICP,
    "ryan_data": RYAN_DATA_ICP,
}


# ── ICP Task Prompt Blocks ───────────────────────────────────────────────────
# Injected into each agent's CrewAI task description so the LLM self-filters.

TYLER_ICP_BLOCK = (
    "\n\n=== ICP GUARDRAILS (MANDATORY) ===\n"
    "ONLY prospect businesses that match ALL of the following criteria:\n"
    "- Located in the DFW 380 Corridor: Aubrey, Celina, Prosper, Pilot Point, or Little Elm TX\n"
    "- Industry: Plumbers, HVAC, Roofers, Dental offices, or Personal Injury Law Firms\n"
    "- Owner-operated, 1-10 employees\n"
    "- TRIGGER EVENTS to look for: missed call complaints in reviews, hiring receptionist, "
    "new location, bad review streak, competitor with better reviews, seasonal demand approaching\n"
    "EXCLUDE: Franchises, chains, businesses already using AI receptionists or virtual receptionist tools\n"
    "If a business does not match these criteria, skip it and find another. Do NOT include off-ICP prospects.\n"
    "=== END ICP GUARDRAILS ===\n"
)

MARCUS_ICP_BLOCK = (
    "\n\n=== ICP GUARDRAILS (MANDATORY) ===\n"
    "ONLY prospect businesses that match ALL of the following criteria:\n"
    "- Located in Texas (any city — statewide)\n"
    "- Vertical: Med Spas, Personal Injury Law Firms, Real Estate Teams/Brokerages, or Custom Home Builders\n"
    "- Owner-operated or small team (not enterprise/corporate)\n"
    "- TRIGGER EVENTS to look for: new location, expansion, leadership change, negative review streak, "
    "competitor making digital moves, new service launch, award/press mention, seasonal shift approaching\n"
    "EXCLUDE: Enterprise companies, national chains, franchises, businesses already using AI receptionists\n"
    "If a business does not match these criteria, skip it and find another. Do NOT include off-ICP prospects.\n"
    "=== END ICP GUARDRAILS ===\n"
)

RYAN_DATA_ICP_BLOCK = (
    "\n\n=== ICP GUARDRAILS (MANDATORY) ===\n"
    "ONLY prospect dealerships that match ALL of the following criteria:\n"
    "- US franchised or independent car dealerships (NATIONWIDE — any state)\n"
    "- TRIGGER EVENTS to look for: ownership change, new GM, declining reviews, "
    "BDC job postings, expansion/renovation, competitor making digital moves, OEM mandate changes\n"
    "EXCLUDE: Dealerships already using AI tools, buy-here-pay-here lots, auction-only, wholesale-only\n"
    "If a dealership does not match these criteria, skip it and find another. Do NOT include off-ICP prospects.\n"
    "=== END ICP GUARDRAILS ===\n"
)

ICP_PROMPT_BLOCKS = {
    "tyler": TYLER_ICP_BLOCK,
    "marcus": MARCUS_ICP_BLOCK,
    "ryan_data": RYAN_DATA_ICP_BLOCK,
}


# ── Validation Logic ─────────────────────────────────────────────────────────

def _lower(val) -> str:
    return str(val or "").lower().strip()


def _matches_any(text: str, keywords: list) -> bool:
    text = text.lower()
    return any(kw in text for kw in keywords)


def validate_prospect(prospect: dict, agent_name: str) -> Tuple[bool, str]:
    """
    Validate a parsed prospect dict against the agent's ICP.

    Returns:
        (True, "") if prospect passes ICP.
        (False, reason) if prospect fails ICP.
    """
    icp = ICP_MAP.get(agent_name)
    if not icp:
        return True, ""

    biz_name = _lower(prospect.get("business_name", ""))
    city = _lower(prospect.get("city", ""))
    biz_type = _lower(prospect.get("business_type", ""))
    # Exclusion checks run ONLY against name + type, never against the
    # research `reason` field. The reason field is the agent's pitch notes
    # (e.g. "could replace their live chat widget") and will naturally
    # mention exclusion-keyword-like phrases as buying signals.
    exclusion_text = f"{biz_name} {biz_type}"

    # ── Tyler ICP checks ──
    if agent_name == "tyler":
        # City check
        if city and not any(c in city for c in icp["target_cities"]):
            return False, f"City '{city}' not in 380 Corridor target cities"

        # Industry check
        if biz_type and not _matches_any(biz_type, icp["target_industries"]):
            return False, f"Industry '{biz_type}' not in Tyler's target industries"

        # Exclusion check (name + type only, not reason)
        if _matches_any(exclusion_text, icp["exclude_keywords"]):
            matched = [kw for kw in icp["exclude_keywords"] if kw in exclusion_text]
            return False, f"Excluded: matched exclusion keywords {matched}"

    # ── Marcus ICP checks ──
    elif agent_name == "marcus":
        # Vertical check — must be one of the 4 target verticals
        if biz_type and not _matches_any(biz_type, icp["target_verticals"]):
            return False, f"Vertical '{biz_type}' not in Marcus's 4 target verticals"

        # Exclusion check (name + type only, not reason)
        if _matches_any(exclusion_text, icp["exclude_keywords"]):
            matched = [kw for kw in icp["exclude_keywords"] if kw in exclusion_text]
            return False, f"Excluded: matched exclusion keywords {matched}"

    # ── Ryan Data ICP checks ──
    elif agent_name == "ryan_data":
        # Exclusion check (name + type only, not reason)
        if _matches_any(exclusion_text, icp["exclude_keywords"]):
            matched = [kw for kw in icp["exclude_keywords"] if kw in exclusion_text]
            return False, f"Excluded: matched exclusion keywords {matched}"

        # Must be a dealership
        dealership_terms = ["dealer", "dealership", "auto", "motor", "cars"]
        if biz_type and not _matches_any(biz_type, dealership_terms):
            return False, f"Business type '{biz_type}' does not appear to be a dealership"

    return True, ""


def validate_and_filter_prospects(
    prospects: list,
    agent_name: str,
) -> Tuple[List[dict], List[dict]]:
    """
    Validate a list of parsed prospects against ICP.

    Returns:
        (valid_prospects, discarded_prospects_with_reasons)
    """
    valid = []
    discarded = []

    for p in prospects:
        passed, reason = validate_prospect(p, agent_name)
        if passed:
            valid.append(p)
        else:
            p_copy = dict(p)
            p_copy["_discard_reason"] = reason
            discarded.append(p_copy)
            logger.info(
                "[ICP] Discarded %s prospect '%s': %s",
                agent_name, p.get("business_name", "unknown"), reason,
            )

    if discarded:
        log_icp_discards(agent_name, discarded)

    logger.info(
        "[ICP] %s: %d passed, %d discarded out of %d prospects",
        agent_name, len(valid), len(discarded), len(prospects),
    )
    return valid, discarded


def log_icp_discards(agent_name: str, discarded: list) -> None:
    """Log each discarded prospect to the icp_discards table."""
    for p in discarded:
        try:
            execute_query(
                "INSERT INTO icp_discards (agent_name, business_name, city, business_type, reason) "
                "VALUES (%s, %s, %s, %s, %s)",
                (
                    agent_name,
                    (p.get("business_name") or "")[:255],
                    (p.get("city") or "")[:128],
                    (p.get("business_type") or "")[:128],
                    (p.get("_discard_reason") or "")[:512],
                ),
            )
        except Exception as e:
            logger.error("[ICP] Failed to log discard for %s: %s", p.get("business_name"), e)
