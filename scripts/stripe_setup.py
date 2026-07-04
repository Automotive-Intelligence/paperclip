"""scripts/stripe_setup.py — One-shot, idempotent Stripe provisioning.

Creates the Stripe Products + Prices for all six approved offers
(config/pricing.yaml) and generates a recurring Payment Link for each, then
prints the link URLs. Safe to run repeatedly: Products are matched by metadata
catalog_key, Prices by lookup_key, and Payment Links by metadata, so a second
run reuses everything instead of duplicating.

Payee entity for the Stripe account: Automotive Intelligence LLC.

USAGE
  # Test mode first (recommended) — use a Stripe TEST secret key:
  STRIPE_SECRET_KEY=sk_test_xxx python scripts/stripe_setup.py

  # Live:
  STRIPE_SECRET_KEY=sk_live_xxx python scripts/stripe_setup.py

  # Dry run — print the plan, touch nothing (no key needed):
  python scripts/stripe_setup.py --dry-run

GUARD: with no STRIPE_SECRET_KEY the script prints the plan and exits 0 without
calling Stripe. It never crashes on a missing key.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from config import pricing  # noqa: E402
from tools import stripe as stripe_tools  # noqa: E402


def _fmt_amount(offer: dict) -> str:
    parts = []
    if offer.get("type") == "recurring":
        parts.append(f"${offer['amount_usd']}/mo")
        if offer.get("setup_usd"):
            parts.append(f"${offer['setup_usd']} setup")
    else:
        parts.append(f"${offer['amount_usd']} one time")
    return " + ".join(parts)


def print_plan() -> None:
    print(f"Payee entity : {pricing.payee_entity()}")
    print(f"Currency     : {pricing.currency()}")
    print("Offers to provision:")
    for o in pricing.all_offers():
        print(f"  - [{o['brand']:>4}] {o['name']:<32} {_fmt_amount(o)}  (key={o['key']})")


def main() -> int:
    ap = argparse.ArgumentParser(description="Provision Stripe Products/Prices/Payment Links.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the plan and exit without calling Stripe.")
    args = ap.parse_args()

    print("=" * 70)
    print("Stripe get-paid rail — provisioning")
    print("=" * 70)
    print_plan()
    print("-" * 70)

    if args.dry_run:
        print("DRY RUN: no Stripe calls made.")
        return 0

    if not stripe_tools.stripe_ready():
        print("STRIPE INACTIVE: STRIPE_SECRET_KEY not set (or `stripe` SDK not "
              "installed). Nothing provisioned. Set the key and re-run.")
        return 0

    mode = "TEST" if (stripe_tools.stripe_secret_key() or "").startswith("sk_test_") else "LIVE"
    print(f"Stripe mode  : {mode}")
    print("-" * 70)

    results = []
    for offer in pricing.all_offers():
        try:
            res = stripe_tools.provision_offer(offer)
            results.append(res)
            print(f"OK  [{res['brand']:>4}] {res['name']}")
            print(f"      link: {res['payment_link_url']}")
        except Exception as e:
            print(f"FAIL [{offer.get('brand','?'):>4}] {offer.get('name','?')}: {e}")

    print("-" * 70)
    print(f"Provisioned {len(results)}/{len(pricing.all_offers())} offers.")
    print("\nPayment Links (paste into SOWs):")
    for r in results:
        print(f"  {r['name']:<32} {r['payment_link_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
