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
    # DFW North metro — all the suburbs in the greater 380 Corridor
    # where real service businesses exist (from verified plumber CSV data)
    "target_cities": [
        "aubrey", "celina", "prosper", "pilot point", "little elm",
        "denton", "frisco", "mckinney", "allen", "plano", "flower mound",
        "lewisville", "the colony", "lake dallas", "argyle", "justin",
        "cross roads", "hebron", "carrollton", "garland", "richardson",
        "sanger", "van alstyne", "forney", "wylie", "murphy", "sachse",
        "princeton", "anna", "krum", "krugerville", "providence village",
        "oak point", "cross roads", "corinth", "lake dallas",
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


# ── Hallucination detection helpers ─────────────────────────────────────────

PLACEHOLDER_NAMES = {
    "john smith", "jane doe", "john doe", "jane smith",
    "bob smith", "mary smith", "mike smith", "test user",
    "first last", "firstname lastname", "unknown prospect",
    "prospect lead", "contact name",
}

PLACEHOLDER_FIRST_NAMES = {
    "john", "jane", "test", "demo", "sample", "example", "prospect",
}

PLACEHOLDER_LAST_NAMES = {
    "smith", "doe", "test", "sample", "example", "unknown", "lead",
}


def _is_placeholder_name(name: str) -> bool:
    """Detect generic placeholder names the LLM uses when hallucinating."""
    if not name:
        return False
    n = name.lower().strip().replace("dr. ", "").replace("dr ", "")
    if n in PLACEHOLDER_NAMES:
        return True
    # Common "firstname lastname" hallucination combos
    parts = n.split()
    if len(parts) == 2:
        first, last = parts
        if first in PLACEHOLDER_FIRST_NAMES and last in PLACEHOLDER_LAST_NAMES:
            return True
    return False


def _has_fake_phone(phone: str) -> bool:
    """Detect LLM-hallucinated phone numbers (sequential, repeated, 555)."""
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if not digits:
        return False
    # Strip US country code
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return False  # malformed, let other validation catch it

    subscriber = digits[3:]  # last 7 digits (after area code)

    # 555 in exchange position (classic Hollywood fake)
    if digits[3:6] == "555":
        return True

    # Check for 4+ consecutive ascending or descending digits anywhere
    # (1234, 2345, 3456, 4567, 5678, 6789, 0123, 9876, 8765, 7654, 6543, 5432, 4321)
    for i in range(len(subscriber) - 3):
        chunk = subscriber[i:i+4]
        d = [int(x) for x in chunk]
        # Ascending
        if d[1] - d[0] == 1 and d[2] - d[1] == 1 and d[3] - d[2] == 1:
            return True
        # Descending
        if d[0] - d[1] == 1 and d[1] - d[2] == 1 and d[2] - d[3] == 1:
            return True

    # Check for 4+ repeated digits in a row (1111, 2222, 5555, 9999)
    for i in range(len(subscriber) - 3):
        if subscriber[i] == subscriber[i+1] == subscriber[i+2] == subscriber[i+3]:
            return True

    # All same digit (0000000, 1111111)
    if len(set(subscriber)) <= 1:
        return True

    # Check full 10-digit number for round/templated patterns
    # e.g., 4698881234 has the 1234 at the end
    last_four = digits[-4:]
    if last_four in {"0000", "1111", "2222", "3333", "4444", "5555",
                     "6666", "7777", "8888", "9999", "1234", "2345",
                     "3456", "4567", "5678", "6789", "7890", "9876",
                     "8765", "4321", "1000", "2000"}:
        return True

    return False


def _has_fake_email_domain(email: str) -> bool:
    """Detect obviously LLM-fabricated email domains."""
    if not email or "@" not in email:
        return False
    domain = email.split("@")[-1].lower()

    # Too-generic business descriptor domains (hallucination signature)
    # Real businesses rarely have domains like "coolbreezehvac.com" or "proplumbingsolutions.com"
    hallucination_patterns = [
        "proplumbing", "coolbreeze", "brightsmile", "smilecraft",
        "righttemp", "stormshield", "stormready", "pipepros",
        "comfortsystems", "rapidrooter", "justicelegal", "lonestarjustice",
    ]
    for pattern in hallucination_patterns:
        if pattern in domain:
            return True
    return False


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
    contact_name = prospect.get("contact_name", "")
    phone = prospect.get("phone", "")
    email = prospect.get("email", "")
    # Exclusion checks run ONLY against name + type, never against the
    # research `reason` field. The reason field is the agent's pitch notes
    # (e.g. "could replace their live chat widget") and will naturally
    # mention exclusion-keyword-like phrases as buying signals.
    exclusion_text = f"{biz_name} {biz_type}"

    # ── Universal hallucination checks (all sales agents) ──
    if _is_placeholder_name(contact_name):
        return False, f"Placeholder contact name detected: '{contact_name}'"
    if _has_fake_phone(phone):
        return False, f"Fake/hallucinated phone pattern: '{phone}'"
    if _has_fake_email_domain(email):
        return False, f"Hallucinated email domain: '{email}'"

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
