"""VERA — Consumer Buyer Agent for CustomerAdvocate.

Collects behavioral signals (not self-reported preferences).
Scores across 6 dimensions.
Assigns negotiation profile.
Knows buyer's real walk-away threshold before they do.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from datetime import datetime
from core.logger import log_info


class NegotiationProfile(Enum):
    DECISIVE = "decisive"          # Knows what they want, moves fast
    ANALYTICAL = "analytical"       # Researches deeply, compares everything
    EMOTIONAL = "emotional"         # Connects to the car, brand loyalty matters
    BUDGET_DRIVEN = "budget_driven" # Price is the dominant factor
    LIFESTYLE = "lifestyle"         # Vehicle fits a life change or aspiration
    FLEXIBLE = "flexible"           # Open to persuasion, no strong anchor


@dataclass
class BehavioralSignal:
    signal_type: str       # browse, compare, return, time_spent, price_check, config
    vehicle_id: str
    timestamp: datetime
    metadata: dict = field(default_factory=dict)


@dataclass
class BuyerProfile:
    buyer_id: str
    signals: list = field(default_factory=list)
    scores: dict = field(default_factory=dict)
    negotiation_profile: Optional[NegotiationProfile] = None
    walk_away_threshold: Optional[float] = None
    created_at: datetime = field(default_factory=datetime.now)


# The 6 scoring dimensions
DIMENSIONS = [
    "urgency",           # How soon do they need to buy?
    "price_sensitivity",  # How much does price drive their decision?
    "brand_loyalty",      # Are they locked to a brand or open?
    "feature_priority",   # Do they care about features over price?
    "research_depth",     # How deep have they gone?
    "commitment_level",   # How close are they to buying?
]


def create_buyer(buyer_id: str) -> BuyerProfile:
    """Create a new buyer profile."""
    profile = BuyerProfile(buyer_id=buyer_id)
    log_info("customer_advocate", f"[VERA] New buyer profile: {buyer_id}")
    return profile


def record_signal(profile: BuyerProfile, signal: BehavioralSignal):
    """Record a behavioral signal for a buyer."""
    profile.signals.append(signal)
    _rescore(profile)
    log_info("customer_advocate", f"[VERA] Signal recorded for {profile.buyer_id}: {signal.signal_type}")


def _rescore(profile: BuyerProfile):
    """Rescore buyer across all 6 dimensions based on accumulated signals."""
    signals = profile.signals
    if not signals:
        return

    scores = {d: 0.0 for d in DIMENSIONS}

    for signal in signals:
        st = signal.signal_type
        meta = signal.metadata

        # Urgency signals
        if st == "return":
            scores["urgency"] += 2.0  # Coming back = more urgent
        if st == "config":
            scores["urgency"] += 3.0  # Configuring = very close
        if st == "time_spent" and meta.get("minutes", 0) > 10:
            scores["urgency"] += 1.0

        # Price sensitivity
        if st == "price_check":
            scores["price_sensitivity"] += 2.0
        if st == "compare" and meta.get("compared_on") == "price":
            scores["price_sensitivity"] += 2.5
        if st == "config" and meta.get("removed_options"):
            scores["price_sensitivity"] += 1.5

        # Brand loyalty
        if st == "browse" and meta.get("same_brand_count", 0) > 3:
            scores["brand_loyalty"] += 3.0
        if st == "compare" and meta.get("cross_brand"):
            scores["brand_loyalty"] -= 1.0

        # Feature priority
        if st == "config" and meta.get("added_options"):
            scores["feature_priority"] += 2.0
        if st == "compare" and meta.get("compared_on") == "features":
            scores["feature_priority"] += 2.0

        # Research depth
        if st == "browse":
            scores["research_depth"] += 0.5
        if st == "compare":
            scores["research_depth"] += 1.5
        if st == "time_spent":
            scores["research_depth"] += meta.get("minutes", 0) * 0.1

        # Commitment level
        if st == "config":
            scores["commitment_level"] += 4.0
        if st == "return":
            scores["commitment_level"] += 2.0
        if st == "price_check":
            scores["commitment_level"] += 1.5

    # Normalize to 0-10
    max_possible = max(max(scores.values()), 1)
    profile.scores = {d: min(round((v / max_possible) * 10, 1), 10.0) for d, v in scores.items()}

    # Assign negotiation profile
    profile.negotiation_profile = _assign_profile(profile.scores)

    # Estimate walk-away threshold
    profile.walk_away_threshold = _estimate_threshold(profile)


def _assign_profile(scores: dict) -> NegotiationProfile:
    """Assign negotiation profile based on dimension scores."""
    top = max(scores, key=scores.get)

    if scores["urgency"] >= 8 and scores["commitment_level"] >= 7:
        return NegotiationProfile.DECISIVE
    if scores["research_depth"] >= 8:
        return NegotiationProfile.ANALYTICAL
    if scores["brand_loyalty"] >= 7:
        return NegotiationProfile.EMOTIONAL
    if scores["price_sensitivity"] >= 8:
        return NegotiationProfile.BUDGET_DRIVEN
    if scores["feature_priority"] >= 7:
        return NegotiationProfile.LIFESTYLE

    return NegotiationProfile.FLEXIBLE


def _estimate_threshold(profile: BuyerProfile) -> float:
    """Estimate walk-away price threshold based on behavioral signals.
    Returns a multiplier (e.g., 0.92 = will walk at 92% of MSRP).
    """
    base = 0.95  # Start at 95% of MSRP

    ps = profile.scores.get("price_sensitivity", 5)
    if ps >= 8:
        base -= 0.05  # Very price sensitive → walks at 90%
    elif ps >= 5:
        base -= 0.02  # Moderate → walks at 93%

    cl = profile.scores.get("commitment_level", 5)
    if cl >= 8:
        base += 0.03  # Very committed → more willing to pay

    return round(base, 3)


def get_buyer_summary(profile: BuyerProfile) -> dict:
    """Get a summary of the buyer for the AATA negotiation protocol."""
    return {
        "buyer_id": profile.buyer_id,
        "scores": profile.scores,
        "profile": profile.negotiation_profile.value if profile.negotiation_profile else "unknown",
        "walk_away_threshold": profile.walk_away_threshold,
        "signal_count": len(profile.signals),
        "created_at": profile.created_at.isoformat(),
    }
