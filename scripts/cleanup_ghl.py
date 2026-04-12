# AVO — AI Business Operating System
# GHL Contact Cleanup
# Removes hallucinated prospects (555 phones, placeholder names, wrong CRM contacts)
# Preserves engaged contacts (clicked-demo-link, email-engaged)
# Salesdroid — April 2026

"""Clean up GHL contacts from Tyler and Marcus's hallucinated prospecting.

Problem: ~633 contacts, ~73% have fake 555 phone numbers, many are
placeholder names (John Smith, Jane Doe), and ~50% are Marcus prospects
that belong in Attio, not GHL.

Strategy:
  1. Fetch all contacts (paginated)
  2. KEEP contacts with engagement tags (clicked-demo-link, email-engaged)
  3. KEEP contacts with real (non-555) phone numbers AND real business domains
  4. DELETE everything else (hallucinated businesses, fake phones, wrong CRM)
  5. Backup full list to JSON before deleting anything

Usage:
    python scripts/cleanup_ghl.py --dry-run          # Audit only, no deletes
    python scripts/cleanup_ghl.py --delete-junk       # Delete junk, keep engaged
    python scripts/cleanup_ghl.py --nuke-marcus        # Also delete marcus-prospect tagged contacts
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = REPO_ROOT / "data" / "backups"
GHL_BASE = "https://services.leadconnectorhq.com"


def ghl_api_key() -> str:
    t = (os.getenv("GHL_API_KEY") or "").strip()
    if not t:
        print("ERROR: GHL_API_KEY not set"); sys.exit(1)
    return t


def ghl_location_id() -> str:
    t = (os.getenv("GHL_LOCATION_ID") or "").strip()
    if not t:
        print("ERROR: GHL_LOCATION_ID not set"); sys.exit(1)
    return t


def ghl_headers() -> dict:
    return {
        "Authorization": f"Bearer {ghl_api_key()}",
        "Version": "2021-07-28",
        "Accept": "application/json",
    }


# ── Fetch all contacts (paginated) ──────────────────────────────────────────

def fetch_all_contacts() -> list:
    """Fetch all GHL contacts, handling pagination."""
    all_contacts = []
    url = f"{GHL_BASE}/contacts/"
    params = {"locationId": ghl_location_id(), "limit": 100}

    page = 1
    while True:
        print(f"  Fetching page {page}...")
        try:
            r = requests.get(url, headers=ghl_headers(), params=params, timeout=30)
            if r.status_code != 200:
                print(f"  ERROR: {r.status_code} {r.text[:200]}")
                break
            data = r.json()
            contacts = data.get("contacts", [])
            all_contacts.extend(contacts)
            print(f"  Got {len(contacts)} contacts (total so far: {len(all_contacts)})")

            meta = data.get("meta", {})
            next_page = meta.get("nextPageUrl")
            start_after = meta.get("startAfter")
            start_after_id = meta.get("startAfterId")

            if not next_page or not contacts:
                break

            params["startAfter"] = start_after
            params["startAfterId"] = start_after_id
            page += 1
            time.sleep(0.5)
        except Exception as e:
            print(f"  EXCEPTION: {e}")
            break

    return all_contacts


# ── Classification ───────────────────────────────────────────────────────────

KEEP_TAGS = {"email-engaged", "clicked-demo-link", "replied", "demo-booked"}

# Real area codes for DFW 380 corridor and Dallas metro
REAL_DFW_AREA_CODES = {"214", "469", "972", "940", "817", "682"}


def has_fake_phone(phone: str) -> bool:
    """Check if phone contains 555 (Hollywood fake number pattern)."""
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return False
    # Check for 555 in the subscriber number (positions 4-6 in a 10-digit number)
    if len(digits) >= 10:
        # Strip leading 1 for US numbers
        local = digits[-10:]
        return local[3:6] == "555"
    return "555" in digits


def has_placeholder_name(name: str) -> bool:
    """Check for obviously fake/placeholder names."""
    lower = (name or "").lower().strip()
    placeholders = [
        "john smith", "jane doe", "john doe", "jane smith",
        "test", "prospect", "unknown", "none none",
    ]
    return any(p in lower for p in placeholders)


def has_engagement_tag(tags: list) -> bool:
    """Check if contact has any engagement tags worth preserving."""
    return bool(set(t.lower() for t in (tags or [])) & KEEP_TAGS)


def is_marcus_prospect(tags: list) -> bool:
    """Check if this is a Marcus prospect (wrong CRM)."""
    return "marcus-prospect" in (tags or [])


def has_real_business_email(email: str) -> bool:
    """Check if email looks like a real business email (not gmail/yahoo/placeholder)."""
    if not email:
        return False
    email = email.lower()
    if email == "none" or "@" not in email:
        return False
    domain = email.split("@")[-1]
    generic = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com"}
    return domain not in generic


def classify_contact(contact: dict) -> tuple:
    """Returns (action, reason) where action is 'keep', 'delete', or 'review'."""
    name = contact.get("contactName", contact.get("name", ""))
    email = contact.get("email", "")
    phone = contact.get("phone", "")
    tags = contact.get("tags", [])
    company = contact.get("companyName", "")

    # ALWAYS keep engaged contacts
    if has_engagement_tag(tags):
        return ("keep", "has engagement tag")

    # Flag placeholder names
    if has_placeholder_name(name):
        return ("delete", f"placeholder name: {name}")

    # Flag fake 555 phones
    if has_fake_phone(phone):
        return ("delete", f"fake 555 phone: {phone}")

    # Flag Marcus prospects (wrong CRM)
    if is_marcus_prospect(tags):
        return ("delete", "marcus-prospect in GHL (belongs in Attio)")

    # Flag contacts with no email
    if not email or email.lower() == "none":
        return ("delete", "no email address")

    # Real phone + real email = review
    if phone and not has_fake_phone(phone) and has_real_business_email(email):
        return ("keep", "real phone + business email")

    # Real business email but no phone
    if has_real_business_email(email):
        return ("review", "business email but no/missing phone — verify manually")

    return ("delete", "no strong signals (no engagement, no real phone, no business email)")


# ── Delete contact ───────────────────────────────────────────────────────────

def delete_contact(contact_id: str) -> bool:
    """Delete a GHL contact by ID."""
    try:
        r = requests.delete(
            f"{GHL_BASE}/contacts/{contact_id}",
            headers=ghl_headers(),
            timeout=15,
        )
        return r.status_code in (200, 201, 204)
    except Exception:
        return False


# ── Driver ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Audit only, no deletes")
    parser.add_argument("--delete-junk", action="store_true", help="Delete junk contacts")
    parser.add_argument("--nuke-marcus", action="store_true", help="Also delete marcus-prospect tagged contacts")
    args = parser.parse_args()

    if not args.dry_run and not args.delete_junk:
        print("ERROR: Specify --dry-run or --delete-junk")
        sys.exit(1)

    print("AVO — GHL Contact Cleanup")
    print(f"Mode: {'DRY RUN (audit only)' if args.dry_run else 'LIVE DELETE'}")
    print(f"Nuke Marcus prospects: {'YES' if args.nuke_marcus else 'NO'}")
    print()

    # Fetch all contacts
    print("Fetching all GHL contacts...")
    contacts = fetch_all_contacts()
    print(f"Total contacts: {len(contacts)}")
    print()

    # Backup before any changes
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"ghl_contacts_backup_{ts}.json"
    with open(backup_path, "w") as f:
        json.dump(contacts, f, indent=2)
    print(f"Backup saved: {backup_path}")
    print()

    # Classify
    keep = []
    delete = []
    review = []

    for c in contacts:
        action, reason = classify_contact(c)

        # If not nuking marcus, downgrade marcus deletes to review
        if not args.nuke_marcus and is_marcus_prospect(c.get("tags", [])) and action == "delete":
            action = "review"
            reason = "marcus-prospect (use --nuke-marcus to delete)"

        entry = {
            "id": c.get("id", ""),
            "name": c.get("contactName", c.get("name", "?")),
            "email": c.get("email", ""),
            "phone": c.get("phone", ""),
            "company": c.get("companyName", ""),
            "tags": c.get("tags", []),
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
    print(f"  KEEP:   {len(keep)}")
    print(f"  DELETE: {len(delete)}")
    print(f"  REVIEW: {len(review)}")
    print()

    print("=== KEEP (engaged or real) ===")
    for e in keep:
        print(f"  ✓ {e['name']:35s} | {e['email']:35s} | {e['reason']}")
    print()

    print("=== REVIEW (needs manual check) ===")
    for e in review[:20]:
        print(f"  ? {(e['name'] or '?'):35s} | {(e['email'] or 'no email'):35s} | {e['reason']}")
    if len(review) > 20:
        print(f"  ... and {len(review) - 20} more")
    print()

    print(f"=== DELETE ({len(delete)} contacts) ===")
    # Show breakdown by reason
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
            if i % 25 == 0:
                print(f"  Progress: {i}/{len(delete)} (deleted={deleted}, failed={failed})")
            time.sleep(0.2)  # Rate limit: 5/sec

        print()
        print("=" * 70)
        print(f"DONE — deleted={deleted} failed={failed} kept={len(keep)} review={len(review)}")
        print("=" * 70)
    elif args.dry_run:
        print("DRY RUN — no contacts deleted. Use --delete-junk to execute.")


if __name__ == "__main__":
    main()
