"""AATA — Automated Automotive Transaction Architecture.

Tamper-proof negotiation protocol between buyer agent and dealer agent.
SSL for car deals — neither side can read the other's threshold.

Phase 2 architecture — foundation laid here.
"""

import hashlib
import json
import uuid
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional
from core.logger import log_info


@dataclass
class NegotiationSession:
    session_id: str
    buyer_id: str
    dealer_id: str
    vehicle_id: str
    buyer_threshold: float     # Buyer's max (hidden from dealer)
    dealer_floor: float        # Dealer's min (hidden from buyer)
    status: str = "open"       # open, matched, no_match, expired
    created_at: datetime = field(default_factory=datetime.now)
    rounds: list = field(default_factory=list)
    final_price: Optional[float] = None

    @property
    def session_hash(self) -> str:
        """Tamper-proof hash of session parameters."""
        payload = json.dumps({
            "session_id": self.session_id,
            "buyer_id": self.buyer_id,
            "dealer_id": self.dealer_id,
            "vehicle_id": self.vehicle_id,
            "created_at": self.created_at.isoformat(),
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class NegotiationRound:
    round_number: int
    buyer_offer: float
    dealer_ask: float
    gap: float
    timestamp: datetime = field(default_factory=datetime.now)


def create_session(buyer_id: str, dealer_id: str, vehicle_id: str,
                   buyer_threshold: float, dealer_floor: float) -> NegotiationSession:
    """Create a new tamper-proof negotiation session."""
    session = NegotiationSession(
        session_id=str(uuid.uuid4()),
        buyer_id=buyer_id,
        dealer_id=dealer_id,
        vehicle_id=vehicle_id,
        buyer_threshold=buyer_threshold,
        dealer_floor=dealer_floor,
    )
    log_info("customer_advocate", f"[AATA] Session created: {session.session_id}")
    log_info("customer_advocate", f"[AATA] Hash: {session.session_hash}")
    return session


def execute_round(session: NegotiationSession, buyer_offer: float, dealer_ask: float) -> dict:
    """Execute a negotiation round. Neither side sees the other's threshold."""
    gap = dealer_ask - buyer_offer

    round_data = NegotiationRound(
        round_number=len(session.rounds) + 1,
        buyer_offer=buyer_offer,
        dealer_ask=dealer_ask,
        gap=gap,
    )
    session.rounds.append(round_data)

    # Check if offers have crossed (deal zone)
    if buyer_offer >= dealer_ask:
        session.status = "matched"
        session.final_price = (buyer_offer + dealer_ask) / 2  # Split the difference
        log_info("customer_advocate", f"[AATA] MATCH in session {session.session_id}: ${session.final_price:,.0f}")
        return {
            "status": "matched",
            "final_price": session.final_price,
            "rounds": len(session.rounds),
        }

    # Check if both sides are within threshold (hidden convergence)
    if buyer_offer >= session.dealer_floor and dealer_ask <= session.buyer_threshold:
        midpoint = (buyer_offer + dealer_ask) / 2
        session.status = "matched"
        session.final_price = midpoint
        log_info("customer_advocate", f"[AATA] THRESHOLD MATCH: ${midpoint:,.0f}")
        return {
            "status": "matched",
            "final_price": midpoint,
            "rounds": len(session.rounds),
        }

    # No match yet — return gap indicator without revealing thresholds
    gap_pct = (gap / dealer_ask) * 100 if dealer_ask > 0 else 0
    hint = "close" if gap_pct < 5 else "moderate" if gap_pct < 10 else "wide"

    log_info("customer_advocate", f"[AATA] Round {round_data.round_number}: gap={hint} ({gap_pct:.1f}%)")
    return {
        "status": "negotiating",
        "round": round_data.round_number,
        "gap_hint": hint,
    }


def get_session_summary(session: NegotiationSession) -> dict:
    """Get session summary — no threshold data exposed."""
    return {
        "session_id": session.session_id,
        "status": session.status,
        "rounds": len(session.rounds),
        "final_price": session.final_price,
        "hash": session.session_hash,
        "created_at": session.created_at.isoformat(),
    }
