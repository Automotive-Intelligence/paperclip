# AVO — AI Business Operating System
# Verify GHL contacts against a source of truth CSV
# Cross-references phones and emails to separate real from hallucinated
# Salesdroid — April 2026

"""Verify GHL contacts against a verified source CSV.

Problem: Tyler's GHL has 221 contacts but many are LLM hallucinations
with fake phone numbers and invented business names. A real lead list
from Facebook/Google Ads custom audience is the source of truth.

Strategy:
  1. Load the verified CSV (phones, emails, business names)
  2. Fetch all Tyler-tagged GHL contacts
  3. For each GHL contact, check if phone or email matches the verified list
  4. Classify: MATCH (real), NO_MATCH (hallucinated), REVIEW
  5. Report summary and optionally delete unmatched contacts

Usage:
    python scripts/verify_ghl_against_source.py --csv data/verified_sources/plumbers_aubrey_verified.csv
    python scripts/verify_ghl_against_source.py --csv <path> --delete-unmatched
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
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
        "Content-Type": "application/json",
    }


def normalize_phone(raw: str) -> str:
    """Extract digits only. Returns 10-digit local number for comparison."""
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return ""
    if len(digits) >= 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits[-10:] if len(digits) >= 10 else digits


def normalize_email(raw: str) -> str:
    return (raw or "").strip().lower()


def load_verified_csv(path: Path) -> tuple:
    """Load verified source CSV. Returns (phone_set, email_set, business_set)."""
    phones = set()
    emails = set()
    businesses = set()

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Try multiple phone column names
            phone_raw = row.get("phone") or row.get("Phone") or ""
            phone = normalize_phone(phone_raw)
            if phone:
                phones.add(phone)

            email_raw = row.get("email") or row.get("Email") or ""
            email = normalize_email(email_raw)
            if email:
                emails.add(email)

            name = row.get("name") or row.get("First Name") or ""
            if name:
                businesses.add(name.lower().strip())

    return phones, emails, businesses


def fetch_all_tyler_contacts() -> list:
    """Fetch all tyler-prospect tagged GHL contacts with pagination."""
    all_contacts = []
    params = {"locationId": ghl_location_id(), "limit": 100}
    url = f"{GHL_BASE}/contacts/"

    while True:
        try:
            r = requests.get(url, headers=ghl_headers(), params=params, timeout=30)
            if r.status_code != 200:
                print(f"  fetch error: {r.status_code}")
                break
            data = r.json()
            contacts = data.get("contacts", [])
            tyler_contacts = [c for c in contacts if "tyler-prospect" in (c.get("tags") or [])]
            all_contacts.extend(tyler_contacts)

            meta = data.get("meta", {})
            if not meta.get("nextPageUrl") or not contacts:
                break

            params["startAfter"] = meta.get("startAfter")
            params["startAfterId"] = meta.get("startAfterId")
            time.sleep(0.3)
        except Exception as e:
            print(f"  fetch exception: {e}")
            break

    return all_contacts


def delete_contact(contact_id: str) -> bool:
    try:
        r = requests.delete(
            f"{GHL_BASE}/contacts/{contact_id}",
            headers=ghl_headers(),
            timeout=15,
        )
        return r.status_code in (200, 201, 204)
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="Path to verified source CSV")
    parser.add_argument("--delete-unmatched", action="store_true", help="Delete contacts that don't match")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"ERROR: CSV not found: {csv_path}")
        sys.exit(1)

    print("AVO — GHL Verification Against Source CSV")
    print(f"Source: {csv_path}")
    print(f"Mode: {'LIVE DELETE UNMATCHED' if args.delete_unmatched else 'AUDIT ONLY'}")
    print()

    # Load verified list
    print("Loading verified source...")
    v_phones, v_emails, v_businesses = load_verified_csv(csv_path)
    print(f"  Verified phones: {len(v_phones)}")
    print(f"  Verified emails: {len(v_emails)}")
    print(f"  Verified businesses: {len(v_businesses)}")
    print()

    # Fetch GHL contacts
    print("Fetching Tyler's GHL contacts...")
    contacts = fetch_all_tyler_contacts()
    print(f"  Total Tyler contacts: {len(contacts)}")
    print()

    # Classify
    matched = []
    unmatched = []

    for c in contacts:
        phone = normalize_phone(c.get("phone") or "")
        email = normalize_email(c.get("email") or "")
        company = (c.get("companyName") or "").lower().strip()
        name = c.get("contactName") or c.get("name") or ""

        phone_match = phone and phone in v_phones
        email_match = email and email in v_emails
        company_match = company and any(b in company or company in b for b in v_businesses)

        entry = {
            "id": c.get("id", ""),
            "name": name,
            "email": email,
            "phone": phone,
            "company": company,
            "match_type": [],
        }

        if phone_match:
            entry["match_type"].append("phone")
        if email_match:
            entry["match_type"].append("email")
        if company_match and not (phone_match or email_match):
            entry["match_type"].append("company_only")

        if entry["match_type"]:
            matched.append(entry)
        else:
            unmatched.append(entry)

    # Report
    print("=" * 70)
    print("VERIFICATION RESULTS")
    print("=" * 70)
    print(f"  MATCHED (real):      {len(matched)}")
    print(f"  UNMATCHED (suspect): {len(unmatched)}")
    print()

    print("=== MATCHED (verified against source) ===")
    for e in sorted(matched, key=lambda x: x["company"]):
        types = ",".join(e["match_type"])
        print(f"  ✓ {(e['name'] or '?')[:30]:30s} | {(e['email'] or 'no email')[:35]:35s} | {e['company'][:30]:30s} | [{types}]")
    print()

    print("=== UNMATCHED (likely hallucinated) ===")
    print(f"  Total: {len(unmatched)}")
    if len(unmatched) <= 30:
        for e in unmatched:
            print(f"  ✗ {(e['name'] or '?')[:30]:30s} | {(e['email'] or 'no email')[:35]:35s} | {e['phone'] or 'no phone':12s} | {e['company'][:30]}")
    else:
        print("  (showing first 20)")
        for e in unmatched[:20]:
            print(f"  ✗ {(e['name'] or '?')[:30]:30s} | {(e['email'] or 'no email')[:35]:35s} | {e['phone'] or 'no phone':12s} | {e['company'][:30]}")
    print()

    # Delete unmatched if requested
    if args.delete_unmatched and unmatched:
        print(f"DELETING {len(unmatched)} unmatched contacts...")
        deleted = 0
        failed = 0
        for i, e in enumerate(unmatched, 1):
            ok = delete_contact(e["id"])
            if ok:
                deleted += 1
            else:
                failed += 1
            if i % 25 == 0:
                print(f"  Progress: {i}/{len(unmatched)} (deleted={deleted}, failed={failed})")
            time.sleep(0.2)
        print()
        print(f"DONE — deleted={deleted} failed={failed} matched_kept={len(matched)}")
    elif not args.delete_unmatched:
        print("AUDIT ONLY — no deletes. Re-run with --delete-unmatched to execute.")


if __name__ == "__main__":
    main()
