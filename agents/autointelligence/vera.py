"""
Consumer Agent - Vera
Represents the buyer in AI-to-AI negotiations
"""

import os
import asyncio
import uuid
import asyncpg
from typing import Dict, Any, Optional
from dotenv import load_dotenv

load_dotenv()


class ConsumerAgent:
    """Vera - AI agent representing the consumer/buyer"""

    def __init__(self, buyer_name: str = "Vera"):
        self.buyer_name = buyer_name
        self.profile = None
        self.pool = None

    async def connect_db(self):
        """Connect to PostgreSQL on Railway"""
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            print("WARNING: DATABASE_URL not set. Running in demo mode.")
            return False

        try:
            self.pool = await asyncpg.create_pool(database_url)
            print("Vera connected to PostgreSQL on Railway")
            return True
        except Exception as e:
            print(f"WARNING: Database connection failed: {e}")
            return False

    def set_buyer_profile(
        self,
        name: str = "Sarah Johnson",
        max_budget: float = 50000,
        max_monthly_payment: float = 650,
        credit_tier: str = "good",
        trade_in_value: float = 18500,
        walk_away_threshold: float = 49000,
    ) -> Dict[str, Any]:
        """Set buyer's preferences and constraints"""
        self.profile = {
            "buyer_id": f"buyer_{uuid.uuid4().hex[:8]}",
            "name": name,
            "max_budget": max_budget,
            "max_monthly_payment": max_monthly_payment,
            "credit_tier": credit_tier,
            "trade_in_value": trade_in_value,
            "walk_away_threshold": walk_away_threshold,
        }
        return self.profile

    def calculate_opening_offer(self, asking_price: float) -> float:
        """
        Calculate opening offer:
        - 5-8% below asking price for new/like-new cars
        - More aggressive (10-12%) for aged inventory or less desirable models
        - Cap at the lower of asking price or walk-away threshold
        """
        discount_pct = 0.07
        opening = asking_price * (1 - discount_pct)
        opening = round(opening / 100) * 100
        # Cap at min(asking_price, walk_away_threshold) if profile is set
        if self.profile and "walk_away_threshold" in self.profile:
            cap = min(asking_price, self.profile["walk_away_threshold"])
            if opening > cap:
                opening = cap
        return opening

    def evaluate_dealer_counter(self, dealer_price: float, current_offer: float, round_num: int) -> Dict[str, Any]:
        """
        Evaluate dealer's counter-offer and decide response.
        Vera's negotiation strategy:
        - If dealer price <= walk_away_threshold, accept
        - If dealer price is close, counter with 50% of the gap
        - If dealer price exceeds walk_away_threshold, escalate to human
        """
        if not self.profile:
            return {
                "action": "error",
                "message": "No buyer profile set",
                "price": None,
            }

        walk_away = self.profile["walk_away_threshold"]
        max_budget = self.profile["max_budget"]

        # Only accept if dealer price is within threshold AND it's round 2 or later
        if dealer_price <= walk_away and round_num >= 2:
            return {
                "action": "accept",
                "price": dealer_price,
                "message": f"ACCEPTED: ${dealer_price:,.0f} is within threshold! (2-round minimum)",
                "reasoning": f"Dealer price ${dealer_price:,.0f} <= walk-away ${walk_away:,.0f} (2-round minimum)",
            }
        # If dealer price is within threshold but it's round 1, always counter to force two rounds
        if dealer_price <= walk_away and round_num == 1:
            gap = dealer_price - current_offer
            counter = current_offer + max(500, gap * 0.5)
            counter = round(counter / 100) * 100
            if counter > max_budget:
                counter = max_budget
            return {
                "action": "counter",
                "price": counter,
                "message": f"COUNTER: We can do ${counter:,.0f}",
                "reasoning": f"Demo mode: always counter on round 1 to force two rounds before accepting.",
            }

        if dealer_price > walk_away:
            return {
                "action": "escalate",
                "price": None,
                "message": f"ESCALATE: ${dealer_price:,.0f} exceeds walk-away threshold of ${walk_away:,.0f}",
                "reasoning": f"Dealer price ${dealer_price:,.0f} > walk-away ${walk_away:,.0f}",
            }

        if round_num >= 5:
            return {
                "action": "escalate",
                "price": None,
                "message": "ESCALATE: Max rounds reached",
                "reasoning": f"Round {round_num} of 5 reached",
            }

        gap = dealer_price - current_offer
        counter = current_offer + (gap * 0.5)
        counter = round(counter / 100) * 100

        if counter > max_budget:
            counter = max_budget

        return {
            "action": "counter",
            "price": counter,
            "message": f"COUNTER: We can do ${counter:,.0f}",
            "reasoning": f"Dealer at ${dealer_price:,.0f}, moving up 50% of gap (${gap:,.0f})",
        }

    async def log_negotiation(
        self,
        session_id: str,
        dealer_session_id: str,
        round_num: int,
        offer: float,
        response: str,
        reasoning: str,
        dealer_price: Optional[float] = None,
    ):
        """Log consumer negotiation to database"""
        if not self.pool:
            return

        try:
            async with self.pool.acquire() as conn:
                if self.profile:
                    await conn.execute(
                        """
                        INSERT INTO consumer.buyer_profiles
                        (buyer_id, name, credit_tier, max_budget, max_monthly_payment, trade_in_value, walk_away_threshold)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        ON CONFLICT (buyer_id) DO NOTHING
                        """,
                        self.profile["buyer_id"],
                        self.profile["name"],
                        self.profile["credit_tier"],
                        self.profile["max_budget"],
                        self.profile["max_monthly_payment"],
                        self.profile["trade_in_value"],
                        self.profile["walk_away_threshold"],
                    )

                session = await conn.fetchrow(
                    "SELECT session_id FROM consumer.negotiation_sessions WHERE session_id = $1",
                    session_id,
                )

                if not session and self.profile:
                    await conn.execute(
                        """
                        INSERT INTO consumer.negotiation_sessions
                        (session_id, buyer_id, dealer_session_id, status, max_price)
                        VALUES ($1, $2, $3, 'active', $4)
                        """,
                        session_id,
                        self.profile["buyer_id"],
                        dealer_session_id,
                        self.profile["max_budget"],
                    )

                await conn.execute(
                    """
                    INSERT INTO consumer.offers (session_id, round_num, offer_amount, decision, reasoning)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    session_id,
                    round_num,
                    offer,
                    response,
                    reasoning,
                )

                print(f"Vera logged to database: Round {round_num}")
        except Exception as e:
            print(f"WARNING: Failed to log to database: {e}")

    async def start_negotiation(self, dealer_agent, vin: str, dealer_session_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Start a negotiation with a dealership agent.
        Simulates full negotiation loop.
        """
        if not self.profile:
            return {"success": False, "error": "Buyer profile not set"}

        vehicle = dealer_agent.get_vehicle(vin)
        if not vehicle:
            return {"success": False, "error": f"Vehicle {vin} not found"}

        asking_price = vehicle["asking_price"]
        session_id = f"consumer_{uuid.uuid4().hex[:8]}"

        if not dealer_session_id:
            dealer_session_id = f"dealer_{uuid.uuid4().hex[:8]}"

        print(f"\nVera negotiating for: {vehicle['year']} {vehicle['make']} {vehicle['model']}")
        print(f"  Asking: ${asking_price:,.0f}")
        print(f"  Max budget: ${self.profile['max_budget']:,.0f}")
        print(f"  Walk-away: ${self.profile['walk_away_threshold']:,.0f}")

        current_offer = self.calculate_opening_offer(asking_price)
        round_num = 1

        print(f"\nRound {round_num}:")
        print(f"  Vera offers: ${current_offer:,.0f}")

        dealer_response = await dealer_agent.start_negotiation(vin, current_offer, round_num, dealer_session_id)

        if not dealer_response["success"]:
            return dealer_response

        print(f"  Dealer: {dealer_response['message']}")

        await self.log_negotiation(
            session_id,
            dealer_session_id,
            round_num,
            current_offer,
            "opening",
            "Opening offer",
            dealer_response.get("dealer_price"),
        )

        while round_num <= 5:
            dealer_price = dealer_response.get("dealer_price")

            if dealer_response["dealer_response"] == "accept":
                print(f"\nDEAL CLOSED at ${dealer_price:,.0f}!")
                return {
                    "success": True,
                    "final_price": dealer_price,
                    "message": f"Deal closed at ${dealer_price:,.0f}",
                    "rounds": round_num,
                    "session_id": session_id,
                    "dealer_session_id": dealer_session_id,
                }

            if dealer_response["dealer_response"] == "reject":
                print("\nDEAL FAILED. Dealer rejected.")
                return {
                    "success": False,
                    "final_price": None,
                    "message": "Dealer rejected",
                    "rounds": round_num,
                }

            eval_result = self.evaluate_dealer_counter(dealer_price, current_offer, round_num)

            print(f"\nRound {round_num + 1}:")
            print(f"  Vera: {eval_result['message']}")

            await self.log_negotiation(
                session_id,
                dealer_session_id,
                round_num + 1,
                eval_result.get("price", current_offer),
                eval_result["action"],
                eval_result["reasoning"],
                dealer_price,
            )

            if eval_result["action"] == "accept":
                print(f"\nDEAL CLOSED at ${dealer_price:,.0f}!")
                return {
                    "success": True,
                    "final_price": dealer_price,
                    "message": f"Vera accepted at ${dealer_price:,.0f}",
                    "rounds": round_num + 1,
                    "session_id": session_id,
                    "dealer_session_id": dealer_session_id,
                }

            if eval_result["action"] == "escalate":
                print("\nESCALATING to human review.")
                return {
                    "success": False,
                    "final_price": None,
                    "message": f"Escalated: {eval_result['reasoning']}",
                    "rounds": round_num + 1,
                    "needs_human": True,
                }

            current_offer = eval_result["price"]
            round_num += 1

            dealer_response = await dealer_agent.start_negotiation(vin, current_offer, round_num, dealer_session_id)

            if not dealer_response["success"]:
                return dealer_response

            print(f"  Dealer: {dealer_response['message']}")

        print(f"\nDEAL FAILED after {round_num} rounds.")
        return {
            "success": False,
            "final_price": None,
            "message": "Max rounds reached",
            "rounds": round_num,
        }


async def test_vera():
    print("=" * 60)
    print("VERA - CONSUMER AGENT")
    print("=" * 60)

    try:
        from michael_meta_ii import DealershipAgent
    except ImportError:
        print("Could not import Michael Meta II. Ensure michael_meta_ii.py is in this directory.")
        return

    dealer = DealershipAgent(dealer_name="Classic Chevrolet")
    await dealer.connect_db()

    vera = ConsumerAgent(buyer_name="Vera")
    await vera.connect_db()

    vera.set_buyer_profile(
        name="Sarah Johnson",
        max_budget=50000,
        max_monthly_payment=650,
        credit_tier="good",
        trade_in_value=18500,
        walk_away_threshold=49000,
    )

    print("\nBuyer Profile:")
    print(f"  Name: {vera.profile['name']}")
    print(f"  Max Budget: ${vera.profile['max_budget']:,.0f}")
    print(f"  Walk-away: ${vera.profile['walk_away_threshold']:,.0f}")

    print("\nDealer Inventory:")
    for car in dealer.get_inventory():
        print(f"  {car['year']} {car['make']} {car['model']} - ${car['price']:,.0f}")

    print("\n" + "=" * 60)
    print("NEGOTIATION DEMO: Vera vs Michael Meta II")
    print("=" * 60)

    result = await vera.start_negotiation(dealer, "1GNSKLE09LR123456")

    print("\n" + "=" * 60)
    print("NEGOTIATION RESULT:")
    print("=" * 60)
    print(f"  Success: {result['success']}")
    if result.get("final_price"):
        print(f"  Final Price: ${result['final_price']:,.0f}")
    else:
        print("  Final Price: Not reached")
    print(f"  Rounds: {result.get('rounds', 'N/A')}")
    print(f"  Message: {result.get('message', 'N/A')}")

    print("\n" + "=" * 60)
    print("Demo complete!")
    print("\nTo see logged data in Railway:")
    print("  SELECT * FROM consumer.offers;")
    print("  SELECT * FROM consumer.negotiation_sessions;")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_vera())