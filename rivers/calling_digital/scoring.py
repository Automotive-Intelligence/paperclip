"""ICP scoring for Worship Digital (Brenda).

Score 7+ = Track B (warm). Under 7 = Track A (cold).

Territory ladder (locked 2026-06-25 to match Marcus + Randy, applied at
runtime 2026-06-28 per CRO RED flag 2026-06-27T21:45Z):
  +3  380 Corridor — Prosper, Celina, Aubrey, Little Elm, Pilot Point,
                    Frisco-adjacent areas closest to the 380 highway
  +2  Greater DFW — Dallas, Plano, McKinney, Frisco, Denton, Arlington,
                    Fort Worth, and the rest of the metroplex
  +1  Texas outside DFW
  +0  National   — trigger-event opportunity only; must clear 7 some
                    other way (vertical + revenue + AI interest + referred)

Prior version collapsed 380/DFW/TX into one bucket and gave the same boost
to a Prosper med-spa as to a Garland one. The graded ladder fixes that
and also closes the Marcus PR #72 territory miss.
"""

# 380 Corridor — small towns hugging US-380 between Denton & McKinney.
# Highest-leverage WD wedge (founder proximity, low ad-density). +3.
TERRITORY_380_CORRIDOR = (
    "prosper", "celina", "aubrey", "little elm", "pilot point",
    "savannah", "providence village", "cross roads", "krugerville",
    "oak point", "paloma creek",
)

# Greater DFW — the rest of the metroplex. +2.
TERRITORY_GREATER_DFW = (
    "dallas", "fort worth", "dfw", "plano", "frisco", "mckinney", "allen",
    "denton", "arlington", "irving", "garland", "richardson", "carrollton",
    "lewisville", "flower mound", "southlake", "grapevine", "colleyville",
    "keller", "north richland hills", "mesquite", "rowlett", "rockwall",
    "the colony", "addison", "farmers branch", "coppell", "euless",
    "bedford", "hurst", "haltom city", "white settlement",
)

# Texas outside DFW. +1.
TERRITORY_TX_KEYWORDS = ("texas", " tx,", " tx ", " tx.", " tx")

TARGET_VERTICALS = ("med-spa", "pi-law", "real-estate", "home-builder")


def _territory_score(location_lc: str) -> int:
    """Graded territory ladder. Order matters — most-specific first so
    'Prosper, TX' resolves to 380 Corridor (+3), not generic TX (+1).
    """
    if any(town in location_lc for town in TERRITORY_380_CORRIDOR):
        return 3
    if any(area in location_lc for area in TERRITORY_GREATER_DFW):
        return 2
    if any(kw in location_lc for kw in TERRITORY_TX_KEYWORDS):
        return 1
    return 0


def score_contact(contact: dict) -> int:
    """Score a contact based on ICP criteria. Returns integer score."""
    score = 0
    city = (contact.get("city") or "").lower()
    state = (contact.get("state") or "").lower()
    # Add framing spaces so the " tx " keyword catches "City, TX" cleanly.
    location = f" {city} {state} "
    vertical = (contact.get("vertical") or "").lower()
    revenue = contact.get("revenue") or 0
    ai_interest = contact.get("ai_interest", False)
    referred = contact.get("referred_by_client", False)
    engaged = contact.get("content_engaged", False)

    # Territory ladder (replaces the flat ±3 logic)
    score += _territory_score(location)

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

    return max(score, 0)


def assign_track(score: int) -> str:
    """Track B for warm (7+), Track A for cold."""
    return "B" if score >= 7 else "A"


def territory_label(contact: dict) -> str:
    """Human-readable territory tier for tally summaries + telemetry."""
    location = f" {(contact.get('city') or '').lower()} {(contact.get('state') or '').lower()} "
    tier = _territory_score(location)
    return {3: "380_corridor", 2: "greater_dfw", 1: "tx_outside", 0: "national"}[tier]
