"""ICP scoring for Calling Digital (Brenda).

Score 7+ = Track B (warm). Under 7 = Track A (cold).
"""

DFW_KEYWORDS = [
    "dallas", "fort worth", "dfw", "plano", "frisco", "mckinney", "allen",
    "prosper", "celina", "aubrey", "little elm", "denton", "arlington",
    "irving", "garland", "richardson", "carrollton", "lewisville",
    "flower mound", "southlake", "grapevine", "colleyville", "keller",
    "north texas", "texas", "tx",
]

TARGET_VERTICALS = ["med-spa", "pi-law", "real-estate", "home-builder"]


def score_contact(contact: dict) -> int:
    """Score a contact based on ICP criteria. Returns integer score."""
    score = 0
    city = (contact.get("city") or "").lower()
    state = (contact.get("state") or "").lower()
    location = f"{city} {state}"
    vertical = (contact.get("vertical") or "").lower()
    revenue = contact.get("revenue") or 0
    ai_interest = contact.get("ai_interest", False)
    referred = contact.get("referred_by_client", False)
    engaged = contact.get("content_engaged", False)

    # +3 North Texas/DFW business
    if any(kw in location for kw in DFW_KEYWORDS):
        score += 3

    # +3 Target vertical match
    if vertical in TARGET_VERTICALS:
        score += 3

    # +2 Revenue over $1M
    if isinstance(revenue, (int, float)) and revenue >= 1_000_000:
        score += 2

    # +2 Expressed AI interest
    if ai_interest:
        score += 2

    # +2 Referred by client
    if referred:
        score += 2

    # +1 Engaged with content
    if engaged:
        score += 1

    # -2 Outside Texas, no referral
    if "texas" not in location and "tx" not in location and not referred:
        if not any(kw in location for kw in DFW_KEYWORDS):
            score -= 2

    return max(score, 0)


def assign_track(score: int) -> str:
    """Track B for warm (7+), Track A for cold."""
    return "B" if score >= 7 else "A"
