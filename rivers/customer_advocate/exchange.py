"""The Exchange — Network Infrastructure for CustomerAdvocate.

Platform connecting all buyer agents to all dealer agents.
Visa between cardholders and merchants — the long game.

Phase 3 architecture — foundation and registry laid here.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from core.logger import log_info


@dataclass
class DealerAgent:
    dealer_id: str
    dealership_name: str
    location: str
    inventory_api: Optional[str] = None
    active: bool = True
    registered_at: datetime = field(default_factory=datetime.now)


@dataclass
class BuyerAgent:
    buyer_id: str
    region: str
    active: bool = True
    registered_at: datetime = field(default_factory=datetime.now)


class Exchange:
    """The Exchange — routes buyer agents to dealer agents."""

    def __init__(self):
        self._dealers: dict[str, DealerAgent] = {}
        self._buyers: dict[str, BuyerAgent] = {}
        self._sessions: list = []
        log_info("customer_advocate", "[EXCHANGE] Initialized")

    def register_dealer(self, dealer: DealerAgent):
        self._dealers[dealer.dealer_id] = dealer
        log_info("customer_advocate", f"[EXCHANGE] Dealer registered: {dealer.dealership_name}")

    def register_buyer(self, buyer: BuyerAgent):
        self._buyers[buyer.buyer_id] = buyer
        log_info("customer_advocate", f"[EXCHANGE] Buyer registered: {buyer.buyer_id}")

    def find_dealers(self, region: str, vehicle_type: Optional[str] = None) -> list[DealerAgent]:
        """Find active dealers in a region."""
        matches = [
            d for d in self._dealers.values()
            if d.active and region.lower() in d.location.lower()
        ]
        log_info("customer_advocate", f"[EXCHANGE] Found {len(matches)} dealers in {region}")
        return matches

    def route_negotiation(self, buyer_id: str, dealer_id: str, vehicle_id: str) -> dict:
        """Route a buyer to a dealer for AATA negotiation."""
        from rivers.customer_advocate.aata import create_session

        buyer = self._buyers.get(buyer_id)
        dealer = self._dealers.get(dealer_id)

        if not buyer or not dealer:
            return {"error": "Buyer or dealer not found"}

        log_info("customer_advocate", f"[EXCHANGE] Routing {buyer_id} → {dealer.dealership_name} for {vehicle_id}")
        return {
            "status": "routed",
            "buyer_id": buyer_id,
            "dealer_id": dealer_id,
            "vehicle_id": vehicle_id,
        }

    @property
    def stats(self) -> dict:
        return {
            "dealers": len(self._dealers),
            "buyers": len(self._buyers),
            "active_dealers": len([d for d in self._dealers.values() if d.active]),
            "active_buyers": len([b for b in self._buyers.values() if b.active]),
        }


# Singleton exchange instance
_exchange = None


def get_exchange() -> Exchange:
    global _exchange
    if _exchange is None:
        _exchange = Exchange()
    return _exchange
