"""
Dealership Agent - Michael Meta II
Connects to Railway PostgreSQL
"""

import asyncio
import os
import uuid
from typing import Any, Dict, List, Optional

import asyncpg
from dotenv import load_dotenv

load_dotenv()


# Demo inventory (fallback if database is empty)
DEMO_INVENTORY = [
    {
        "vin": "1GNAZ1E41KZ123456",
        "year": 2025,
        "make": "Chevrolet",
        "model": "Equinox",
        "trim": "Premier",
        "color": "Black",
        "msrp": 42880,
        "invoice": 39500,
        "holdback": 500,
        "floor_price": 39000,
        "asking_price": 42880,
        "days_in_inventory": 45,
    },
    {
        "vin": "1GNSKLE09LR123456",
        "year": 2024,
        "make": "Chevrolet",
        "model": "Silverado",
        "trim": "LTZ",
        "color": "White",
        "msrp": 52500,
        "invoice": 48500,
        "holdback": 600,
        "floor_price": 47900,
        "asking_price": 52500,
        "days_in_inventory": 62,
    },
    {
        "vin": "1G1FB1RX0K0123456",
        "year": 2023,
        "make": "Chevrolet",
        "model": "Malibu",
        "trim": "LT",
        "color": "Silver",
        "msrp": 28900,
        "invoice": 27100,
        "holdback": 400,
        "floor_price": 26700,
        "asking_price": 28900,
        "days_in_inventory": 28,
    },
]


class DealershipAgent:
    """Michael Meta II - AI agent representing the dealership."""

    def __init__(self, dealer_name: str = "Classic Chevrolet"):
        self.dealer_name = dealer_name
        self.inventory = DEMO_INVENTORY
        self.pool: Optional[asyncpg.Pool] = None

    async def connect_db(self) -> bool:
        """Connect to PostgreSQL on Railway."""
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            print("WARNING: DATABASE_URL not set. Running in demo mode (no database logging).")
            return False

        try:
            self.pool = await asyncpg.create_pool(database_url)
            print("Connected to PostgreSQL on Railway")

            # Check if inventory exists in database, if not, seed it.
            await self.seed_inventory()
            return True
        except Exception as exc:  # pragma: no cover - environment dependent
            print(f"WARNING: Database connection failed: {exc}")
            print("Running in demo mode (no database logging)")
            return False

    async def seed_inventory(self) -> None:
        """Seed inventory table with demo data if empty."""
        if not self.pool:
            return

        try:
            async with self.pool.acquire() as conn:
                count = await conn.fetchval("SELECT COUNT(*) FROM dealership.inventory")
                if count == 0:
                    print("Seeding inventory with demo vehicles...")
                    for vehicle in DEMO_INVENTORY:
                        await conn.execute(
                            """
                            INSERT INTO dealership.inventory
                            (vin, year, make, model, trim, color, msrp, invoice, holdback, floor_price, asking_price, days_in_inventory)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                            ON CONFLICT (vin) DO NOTHING
                            """,
                            vehicle["vin"],
                            vehicle["year"],
                            vehicle["make"],
                            vehicle["model"],
                            vehicle["trim"],
                            vehicle["color"],
                            vehicle["msrp"],
                            vehicle["invoice"],
                            vehicle["holdback"],
                            vehicle["floor_price"],
                            vehicle["asking_price"],
                            vehicle["days_in_inventory"],
                        )
                    print("Inventory seeded")
        except Exception as exc:  # pragma: no cover - environment dependent
            print(f"WARNING: Could not seed inventory: {exc}")

    async def log_negotiation(
        self,
        session_id: str,
        vin: str,
        round_num: int,
        offer: float,
        response: str,
        reasoning: str,
    ) -> None:
        """Log negotiation to database."""
        if not self.pool:
            return

        try:
            async with self.pool.acquire() as conn:
                session = await conn.fetchrow(
                    "SELECT session_id FROM dealership.negotiation_sessions WHERE session_id = $1",
                    session_id,
                )

                if not session:
                    await conn.execute(
                        """
                        INSERT INTO dealership.negotiation_sessions (session_id, vin, status)
                        VALUES ($1, $2, 'active')
                        """,
                        session_id,
                        vin,
                    )

                await conn.execute(
                    """
                    INSERT INTO dealership.offers (session_id, round_num, offer_amount, decision, reasoning)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    session_id,
                    round_num,
                    offer,
                    response,
                    reasoning,
                )

                print(f"Logged to database: round {round_num}")
        except Exception as exc:  # pragma: no cover - environment dependent
            print(f"WARNING: Failed to log to database: {exc}")

    def get_inventory(self) -> List[Dict[str, Any]]:
        """Return available inventory."""
        return [
            {
                "vin": v["vin"],
                "year": v["year"],
                "make": v["make"],
                "model": v["model"],
                "trim": v["trim"],
                "color": v["color"],
                "price": v["asking_price"],
                "msrp": v["msrp"],
                "days": v["days_in_inventory"],
            }
            for v in self.inventory
        ]

    def get_vehicle(self, vin: str) -> Optional[Dict[str, Any]]:
        """Get a specific vehicle by VIN."""
        for vehicle in self.inventory:
            if vehicle["vin"] == vin:
                return vehicle.copy()
        return None

    @staticmethod
    def calculate_urgency_multiplier(days: int) -> float:
        """Dealers get more flexible as inventory ages."""
        if days < 30:
            return 1.0
        if days < 60:
            return 0.98
        if days < 90:
            return 0.95
        return 0.92

    def evaluate_offer(self, vehicle: Dict[str, Any], offer: float, round_num: int) -> Dict[str, Any]:
        """Core negotiation logic."""
        floor = vehicle["floor_price"]
        asking = vehicle["asking_price"]
        days = vehicle["days_in_inventory"]

        urgency = self.calculate_urgency_multiplier(days)
        effective_floor = floor * urgency

        # Only accept if offer meets floor AND it's round 2 (never earlier)
        if offer >= effective_floor and round_num == 2:
            return {
                "action": "accept",
                "price": offer,
                "message": f"ACCEPTED: We'll do ${offer:,.0f}",
                "reasoning": f"Offer of ${offer:,.0f} meets effective floor of ${effective_floor:,.0f} (2-round minimum)",
            }
        # If offer meets floor but it's round 1, always counter to force two rounds
        if offer >= effective_floor and round_num == 1:
            gap = effective_floor - offer
            counter = offer + max(500, gap * 0.3)  # Always counter up by at least $500
            counter = round(counter / 100) * 100
            if counter < effective_floor:
                counter = effective_floor
            if counter > asking:
                counter = asking
            return {
                "action": "counter",
                "price": counter,
                "message": f"COUNTER: We can do ${counter:,.0f}",
                "reasoning": f"Demo mode: always counter on round 1 to force two rounds before accepting.",
            }

        if round_num >= 5:
            return {
                "action": "reject",
                "price": effective_floor,
                "message": f"REJECTED: Best we can do is ${effective_floor:,.0f}",
                "reasoning": f"Max rounds reached. Final position: ${effective_floor:,.0f}",
            }

        gap = effective_floor - offer
        if gap > 0:
            counter = offer + (gap * 0.3)
        else:
            counter = offer - (abs(gap) * 0.4)

        counter = round(counter / 100) * 100

        if counter < effective_floor:
            counter = effective_floor
        if counter > asking:
            counter = asking

        return {
            "action": "counter",
            "price": counter,
            "message": f"COUNTER: We can do ${counter:,.0f}",
            "reasoning": f"Floor: ${effective_floor:,.0f}, Your offer: ${offer:,.0f}, Gap: ${gap:,.0f}",
        }

    async def start_negotiation(
        self,
        vin: str,
        buyer_offer: float,
        round_num: int = 1,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Start or continue a negotiation."""
        vehicle = self.get_vehicle(vin)
        if not vehicle:
            return {"success": False, "error": f"Vehicle {vin} not found"}

        if not session_id:
            session_id = f"dealer_{uuid.uuid4().hex[:8]}"

        result = self.evaluate_offer(vehicle, buyer_offer, round_num)

        await self.log_negotiation(
            session_id,
            vin,
            round_num,
            buyer_offer,
            result["action"],
            result["reasoning"],
        )

        return {
            "success": True,
            "session_id": session_id,
            "dealer": self.dealer_name,
            "vehicle": f"{vehicle['year']} {vehicle['make']} {vehicle['model']}",
            "vin": vin,
            "round": round_num,
            "buyer_offer": buyer_offer,
            "dealer_response": result["action"],
            "dealer_price": result.get("price"),
            "message": result["message"],
            "reasoning": result["reasoning"],
        }


async def test_agent() -> None:
    print("=" * 60)
    print("MICHAEL META II - DEALERSHIP AGENT")
    print("=" * 60)

    agent = DealershipAgent()
    await agent.connect_db()

    print("\nINVENTORY:")
    for car in agent.get_inventory():
        print(f"  {car['year']} {car['make']} {car['model']} - ${car['price']:,.0f} (days: {car['days']})")

    print("\n" + "=" * 60)
    print("TEST NEGOTIATIONS:")
    print("=" * 60)

    print("\n1) Buyer offers $38,000 on 2025 Equinox Premier")
    result = await agent.start_negotiation("1GNAZ1E41KZ123456", 38000)
    print(f"   {result['message']}")
    print(f"   Reasoning: {result['reasoning']}")

    print("\n2) Buyer offers $48,000 on 2024 Silverado LTZ")
    result = await agent.start_negotiation("1GNSKLE09LR123456", 48000)
    print(f"   {result['message']}")
    print(f"   Reasoning: {result['reasoning']}")

    print("\n3) Buyer offers $28,000 on 2023 Malibu LT")
    result = await agent.start_negotiation("1G1FB1RX0K0123456", 28000)
    print(f"   {result['message']}")
    print(f"   Reasoning: {result['reasoning']}")

    print("\n" + "=" * 60)
    print("MULTI-ROUND NEGOTIATION DEMO:")
    print("=" * 60)
    print("\nNegotiating on 2024 Silverado LTZ")

    vin = "1GNSKLE09LR123456"
    current_offer = 45000
    round_num = 1
    session_id = f"demo_{uuid.uuid4().hex[:8]}"

    while round_num <= 5:
        result = await agent.start_negotiation(vin, current_offer, round_num, session_id)
        print(f"\nRound {round_num}:")
        print(f"   Buyer offers: ${current_offer:,.0f}")
        print(f"   Dealer: {result['message']}")

        if result["dealer_response"] == "accept":
            print(f"\nDEAL CLOSED at ${result['dealer_price']:,.0f}!")
            break
        if result["dealer_response"] == "reject":
            print(f"\nDEAL FAILED. Best offer was ${result['dealer_price']:,.0f}")
            break

        dealer_price = result["dealer_price"]
        if dealer_price:
            current_offer = current_offer + ((dealer_price - current_offer) * 0.5)
            current_offer = round(current_offer / 100) * 100
        round_num += 1

    print("\n" + "=" * 60)
    print("Demo complete!")
    print("\nTo see logged data in Railway:")
    print("   SELECT * FROM dealership.offers;")
    print("   SELECT * FROM dealership.negotiation_sessions;")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_agent())