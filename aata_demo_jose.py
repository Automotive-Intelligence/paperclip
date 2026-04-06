"""
AATA Demo - Jose Puente Meeting
Tuesday, March 2026
"""

import asyncio
import sys

sys.path.append('.')

from agents.autointelligence.michael_meta_ii import DealershipAgent
from agents.autointelligence.vera import ConsumerAgent


async def run_demo():
    print("=" * 70)
    print("AATA - AUTOMOTIVE AI TRANSACTION AUTHORITY")
    print("Demo for Jose Puente")
    print("=" * 70)

    # Initialize agents
    print("\nInitializing agents...")
    dealer = DealershipAgent(dealer_name="Classic Chevrolet")
    await dealer.connect_db()

    vera = ConsumerAgent(buyer_name="Vera")
    await vera.connect_db()

    # Show inventory
    print("\nDEALER INVENTORY:")
    for car in dealer.get_inventory():
        print(f"   {car['year']} {car['make']} {car['model']} - ${car['price']:,.0f} (days on lot: {car['days']})")

    # Set buyer profile
    print("\nBUYER PROFILE:")
    vera.set_buyer_profile(
        name="Sarah Johnson",
        max_budget=50000,
        max_monthly_payment=650,
        credit_tier="good",
        trade_in_value=18500,
        walk_away_threshold=49000,
    )
    print(f"   Name: {vera.profile['name']}")
    print(f"   Max Budget: ${vera.profile['max_budget']:,.0f}")
    print(f"   Walk-away Threshold: ${vera.profile['walk_away_threshold']:,.0f}")
    print(f"   Trade-in Value: ${vera.profile['trade_in_value']:,.0f}")

    print("\n" + "=" * 70)
    print("AI-TO-AI NEGOTIATION STARTING")
    print("=" * 70)

    # Negotiate on Silverado
    vin = "1GNSKLE09LR123456"  # 2024 Silverado LTZ

    result = await vera.start_negotiation(dealer, vin)

    print("\n" + "=" * 70)
    print("NEGOTIATION RESULT")
    print("=" * 70)

    if result.get("success"):
        print("   DEAL CLOSED!")
        print(f"   Final Price: ${result['final_price']:,.0f}")
        print(f"   Rounds: {result.get('rounds', 'N/A')}")
        print(f"   Dealer Session: {result.get('dealer_session_id', 'N/A')}")
        print(f"   Consumer Session: {result.get('session_id', 'N/A')}")
    else:
        print("   DEAL NOT REACHED")
        print(f"   Message: {result.get('message', 'No deal')}")

    print("\n" + "=" * 70)
    print("VERIFY IN DATABASE")
    print("=" * 70)
    print("Run these queries in Railway to see logged data:")
    print("")
    print("   -- Dealer side")
    print("   SELECT * FROM dealership.offers ORDER BY created_at DESC LIMIT 10;")
    print("")
    print("   -- Consumer side")
    print("   SELECT * FROM consumer.offers ORDER BY created_at DESC LIMIT 10;")
    print("")
    print("   -- Combined view")
    print("   SELECT d.session_id, d.round_num, d.offer_amount as dealer_offer,")
    print("          c.offer_amount as consumer_offer, d.decision as dealer_decision")
    print("   FROM dealership.offers d")
    print("   JOIN consumer.offers c ON c.round_num = d.round_num")
    print("   WHERE c.session_id = (SELECT session_id FROM consumer.negotiation_sessions")
    print("                         WHERE dealer_session_id = d.session_id)")
    print("   ORDER BY d.round_num;")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_demo())
