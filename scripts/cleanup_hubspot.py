# AVO — AI Business Operating System
# HubSpot Contact Cleanup
# Removes inbox noise (Stripe, Discord, notifications) and non-dealership contacts
# Preserves real dealership contacts for Ryan Data's Instantly campaigns
# Salesdroid — April 2026

"""Clean up HubSpot contacts from inbox auto-sync noise.

Problem: HubSpot auto-imported every email that hit the inbox —
Stripe invoices, Discord notifications, SaaS vendors, Oura Ring orders.
Mixed in with ~50-80 real dealership contacts.

Strategy:
  1. Fetch all contacts (paginated)
  2. KEEP contacts with dealership email domains (@*ford.com, @*toyota.com, etc.)
  3. KEEP contacts with company names containing dealership terms
  4. DELETE inbox noise (Stripe, Discord, X, Oura, HeyGen, etc.)
  5. DELETE SaaS vendors (not prospects)
  6. REVIEW contacts that might be real but need verification
  7. Backup full list before deleting

Usage:
    python scripts/cleanup_hubspot.py --dry-run          # Audit only
    python scripts/cleanup_hubspot.py --delete-junk       # Delete noise, keep dealers
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = REPO_ROOT / "data" / "backups"
HUBSPOT_BASE = "https://api.hubapi.com"


def hubspot_token() -> str:
    t = (os.getenv("HUBSPOT_ACCESS_TOKEN") or "").strip()
    if not t:
        print("ERROR: HUBSPOT_ACCESS_TOKEN not set"); sys.exit(1)
    return t


def hubspot_headers() -> dict:
    return {
        "Authorization": f"Bearer {hubspot_token()}",
        "Content-Type": "application/json",
    }


# ── Fetch all contacts (paginated) ──────────────────────────────────────────

def fetch_all_contacts() -> list:
    """Fetch all HubSpot contacts with key properties, handling pagination."""
    all_contacts = []
    url = f"{HUBSPOT_BASE}/crm/v3/objects/contacts"
    params = {
        "limit": 100,
        "properties": "firstname,lastname,email,phone,company,city,hs_lead_status,createdate",
    }

    page = 1
    while True:
        print(f"  Fetching page {page}...")
        try:
            r = requests.get(url, headers=hubspot_headers(), params=params, timeout=30)
            if r.status_code != 200:
                print(f"  ERROR: {r.status_code} {r.text[:200]}")
                break
            data = r.json()
            results = data.get("results", [])
            all_contacts.extend(results)
            print(f"  Got {len(results)} contacts (total so far: {len(all_contacts)})")

            paging = data.get("paging", {})
            next_link = paging.get("next", {}).get("after")
            if not next_link or not results:
                break

            params["after"] = next_link
            page += 1
            time.sleep(0.3)
        except Exception as e:
            print(f"  EXCEPTION: {e}")
            break

    return all_contacts


# ── Classification ───────────────────────────────────────────────────────────

# Domains that are clearly NOT dealership prospects — inbox noise
JUNK_DOMAINS = {
    "stripe.com", "discord.com", "x.com", "ouraring.com", "gong.io",
    "heygen.com", "elevenlabs.io", "anthropic.com", "hex.tech",
    "hubspot.com", "readytensor.ai", "viewstats.com", "narvar.com",
    "reply.drifttmail.com", "nifty.com",
}

# Domains of SaaS vendors (not prospects, but industry contacts)
VENDOR_DOMAINS = {
    "authenticom.com", "cdk.com", "carwars.com", "glo3d.com",
    "dealerai.com", "captureds.io", "uveye.com", "qoreai.com",
    "onboardian.com", "quantumconnectai.com", "fasthippomedia.com",
    "evenflow.ai", "motoacquire.com", "visquanta.com",
    "autoacquireai.com", "igniteups.ai",
}

# Patterns that indicate inbox junk
JUNK_EMAIL_PATTERNS = [
    r"invoice\+",
    r"billing\+",
    r"upcoming-invoice\+",
    r"no_reply@",
    r"no-reply@",
    r"notify@",
    r"notifications@",
    r"orders@",
    r"verify@",
    r"reminder@",
    r"news@",
    r"hello@readytensor",
]

# Dealership signals in email domain
DEALER_DOMAIN_TERMS = [
    "ford", "toyota", "honda", "nissan", "chevrolet", "chevy", "kia",
    "infiniti", "lexus", "bmw", "mercedes", "audi", "jeep", "dodge",
    "chrysler", "ram", "subaru", "hyundai", "mazda", "volkswagen", "vw",
    "acura", "volvo", "lincoln", "buick", "gmc", "cadillac", "pontiac",
    "auto", "motor", "dealer", "cars", "peterbilt",
]

# DFW area cities
DFW_CITIES = {
    "dallas", "fort worth", "plano", "frisco", "mckinney", "arlington",
    "irving", "garland", "grand prairie", "mesquite", "denton", "lewisville",
    "richardson", "allen", "flower mound", "mansfield", "north richland hills",
    "rowlett", "euless", "bedford", "hurst", "grapevine", "carrollton",
    "coppell", "southlake", "keller", "colleyville", "rockwall", "wylie",
    "prosper", "celina", "aubrey", "little elm", "pilot point", "anna",
    "princeton", "murphy", "sachse", "forney", "midlothian", "waxahachie",
    "desoto", "cedar hill", "duncanville", "lancaster", "red oak",
}

import re


def classify_contact(contact: dict) -> tuple:
    """Returns (action, reason) where action is 'keep', 'delete', or 'review'."""
    props = contact.get("properties", {})
    email = (props.get("email") or "").lower().strip()
    firstname = props.get("firstname") or ""
    lastname = props.get("lastname") or ""
    name = f"{firstname} {lastname}".strip()
    company = (props.get("company") or "").lower()
    city = (props.get("city") or "").lower()
    phone = props.get("phone") or ""

    domain = email.split("@")[-1] if "@" in email else ""

    # Check junk email patterns first
    for pattern in JUNK_EMAIL_PATTERNS:
        if re.search(pattern, email):
            return ("delete", f"inbox noise pattern: {email}")

    # Check junk domains
    if domain in JUNK_DOMAINS:
        return ("delete", f"junk domain: {domain}")

    # Check vendor domains (not prospects, but could be partners)
    if domain in VENDOR_DOMAINS:
        return ("review", f"vendor/partner: {domain}")

    # Check for obviously spammy names
    spammy_names = ["none none", "none", ""]
    if name.lower() in spammy_names and not company:
        # No name AND no company — but check if email looks like a dealer
        for term in DEALER_DOMAIN_TERMS:
            if term in domain:
                return ("keep", f"nameless but dealer domain: {domain}")
        return ("delete", "no name, no company, non-dealer email")

    # Check for dealership signals
    is_dealer_domain = any(term in domain for term in DEALER_DOMAIN_TERMS)
    is_dealer_company = any(term in company for term in DEALER_DOMAIN_TERMS)
    is_dfw = city in DFW_CITIES

    if is_dealer_domain or is_dealer_company:
        if is_dfw:
            return ("keep", f"DFW dealership contact: {company or domain}")
        else:
            return ("review", f"dealership but not DFW ({city or 'no city'}): {company or domain}")

    # Check for generic/spam email domains
    spam_tlds = [".shop", ".work", ".help", ".info", ".agency", ".org"]
    if any(domain.endswith(tld) for tld in spam_tlds):
        return ("delete", f"spam TLD: {domain}")

    # Gmail/personal emails with no company
    personal_domains = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com"}
    if domain in personal_domains and not company:
        return ("review", f"personal email, no company: {email}")

    # Has a company name — could be a real contact
    if company:
        return ("review", f"has company but unclear if dealer: {company}")

    return ("review", f"unclear: {email}")


# ── Delete contact ───────────────────────────────────────────────────────────

def delete_contact(contact_id: str) -> bool:
    """Archive (delete) a HubSpot contact by ID."""
    try:
        r = requests.delete(
            f"{HUBSPOT_BASE}/crm/v3/objects/contacts/{contact_id}",
            headers=hubspot_headers(),
            timeout=15,
        )
        return r.status_code in (200, 204)
    except Exception:
        return False


# ── Driver ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Audit only, no deletes")
    parser.add_argument("--delete-junk", action="store_true", help="Delete noise contacts")
    args = parser.parse_args()

    if not args.dry_run and not args.delete_junk:
        print("ERROR: Specify --dry-run or --delete-junk")
        sys.exit(1)

    print("AVO — HubSpot Contact Cleanup")
    print(f"Mode: {'DRY RUN (audit only)' if args.dry_run else 'LIVE DELETE'}")
    print()

    # Fetch all contacts
    print("Fetching all HubSpot contacts...")
    contacts = fetch_all_contacts()
    print(f"Total contacts: {len(contacts)}")
    print()

    # Backup
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"hubspot_contacts_backup_{ts}.json"
    with open(backup_path, "w") as f:
        json.dump(contacts, f, indent=2)
    print(f"Backup saved: {backup_path}")
    print()

    # Classify
    keep = []
    delete = []
    review = []

    for c in contacts:
        props = c.get("properties", {})
        action, reason = classify_contact(c)

        entry = {
            "id": c.get("id", ""),
            "name": f"{props.get('firstname', '')} {props.get('lastname', '')}".strip(),
            "email": props.get("email", ""),
            "company": props.get("company", ""),
            "city": props.get("city", ""),
            "phone": props.get("phone", ""),
            "created": (props.get("createdate", ""))[:10],
            "reason": reason,
        }

        if action == "keep":
            keep.append(entry)
        elif action == "delete":
            delete.append(entry)
        else:
            review.append(entry)

    # Print summary
    print("=" * 70)
    print("CLASSIFICATION SUMMARY")
    print("=" * 70)
    print(f"  KEEP:   {len(keep)} (real dealership contacts)")
    print(f"  DELETE: {len(delete)} (inbox noise / junk)")
    print(f"  REVIEW: {len(review)} (needs manual check)")
    print()

    print("=== KEEP (real dealership contacts) ===")
    for e in keep:
        print(f"  ✓ {e['name']:35s} | {e['email']:40s} | {e['company'] or 'no company':30s} | {e['city'] or 'no city'}")
    print()

    print("=== REVIEW (needs manual check) ===")
    for e in review:
        print(f"  ? {(e['name'] or '?'):35s} | {(e['email'] or 'no email'):40s} | {(e['company'] or 'no company'):30s} | {e['reason']}")
    print()

    print(f"=== DELETE ({len(delete)} contacts) ===")
    from collections import Counter
    reasons = Counter(e["reason"].split(":")[0].strip() for e in delete)
    for reason, count in reasons.most_common():
        print(f"  {count:4d} — {reason}")
    print()

    # Execute deletes
    if args.delete_junk and delete:
        print(f"DELETING {len(delete)} junk contacts...")
        deleted = 0
        failed = 0
        for i, e in enumerate(delete, 1):
            ok = delete_contact(e["id"])
            if ok:
                deleted += 1
            else:
                failed += 1
            if i % 10 == 0:
                print(f"  Progress: {i}/{len(delete)} (deleted={deleted}, failed={failed})")
            time.sleep(0.15)  # HubSpot rate limit: ~10/sec

        print()
        print("=" * 70)
        print(f"DONE — deleted={deleted} failed={failed} kept={len(keep)} review={len(review)}")
        print("=" * 70)
    elif args.dry_run:
        print("DRY RUN — no contacts deleted. Use --delete-junk to execute.")


if __name__ == "__main__":
    main()
