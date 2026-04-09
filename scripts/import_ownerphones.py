# AVO — AI Business Operating System
# OwnerPhones CSV → Attio Importer
# One-shot import of 200 contacts across 4 verticals
# Salesdroid — April 2026

"""Import OwnerPhones CSVs into Attio People with vertical tagging.

Usage:
    python scripts/import_ownerphones.py --dry-run    # show first 3 per file
    python scripts/import_ownerphones.py              # live import

Reads from data/ownerphones/*.csv. Each CSV is mapped to a vertical
(med-spa, pi-law, real-estate, home-builder) by filename match.

Sets the custom 'vertical' attribute on each Attio People record so
Brenda can query and enroll them.
"""

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "ownerphones"

ATTIO_BASE = "https://api.attio.com/v2"

# Filename pattern → vertical slug
FILE_VERTICAL_MAP = {
    "med-spa": "michael-med-spa-texas-mobile-phones.csv",
    "pi-law": "michael-personal-injury-texas-mobile-numbers.csv",
    "real-estate": "Michael-texas-real-estate-mobile-phones.csv",
    "home-builder": "michael-custom-home-texas-mobile-phones.csv",
}


def attio_token() -> str:
    token = (os.getenv("ATTIO_API_KEY") or "").strip()
    if not token:
        print("ERROR: ATTIO_API_KEY not set")
        sys.exit(1)
    return token


def attio_headers() -> dict:
    return {
        "Authorization": f"Bearer {attio_token()}",
        "Content-Type": "application/json",
    }


def normalize_phone(raw: str) -> str:
    """Attio wants E.164 format. OwnerPhones gives us 12103805509 → +12103805509."""
    digits = "".join(c for c in (raw or "") if c.isdigit())
    if not digits:
        return ""
    if not digits.startswith("1") and len(digits) == 10:
        digits = "1" + digits
    return f"+{digits}"


def search_person_by_email(email: str) -> str | None:
    """Return existing person record_id if email already in Attio, else None."""
    if not email:
        return None
    payload = {
        "filter": {
            "email_addresses": {"email_address": {"$eq": email.lower()}}
        }
    }
    try:
        r = requests.post(
            f"{ATTIO_BASE}/objects/people/records/query",
            headers=attio_headers(),
            json=payload,
            timeout=15,
        )
        if r.status_code == 200:
            results = r.json().get("data", [])
            if results:
                rid = results[0].get("id", {})
                return rid.get("record_id") if isinstance(rid, dict) else None
    except Exception as e:
        print(f"  search error: {e}")
    return None


def build_payload(row: dict, vertical: str) -> dict:
    """Map a CSV row to an Attio People record payload."""
    first = (row.get("first_name") or "").strip()
    last = (row.get("last_name") or "").strip()
    full = (row.get("full_name") or f"{first} {last}").strip()
    email = (row.get("email") or "").strip().lower()
    phone = normalize_phone(row.get("mobile_phone1") or row.get("phone_number1") or "")
    company = (row.get("company") or "").strip()
    title = (row.get("title") or "").strip()
    locality = (row.get("locality") or "").strip()
    region = (row.get("region") or "").strip()
    linkedin = (row.get("linkedin") or "").strip()
    domain = (row.get("company_domain") or row.get("domain") or "").strip()

    description = (
        f"OwnerPhones import [{vertical}]. "
        f"Title: {title}. Company: {company}. "
        f"Location: {locality}, {region}. "
        f"Domain: {domain}."
    )

    values = {
        "name": [{
            "first_name": first or "Prospect",
            "last_name": last or "Unknown",
            "full_name": full or f"{first} {last}".strip() or "Unknown Prospect",
        }],
        # Attio select attributes accept the option title as a plain string
        "vertical": vertical,
        "description": [{"value": description}],
        "job_title": [{"value": title}] if title else [],
    }

    if email:
        values["email_addresses"] = [{"email_address": email}]
    if phone:
        values["phone_numbers"] = [{"original_phone_number": phone}]
    if linkedin:
        values["linkedin"] = [{"value": linkedin}]

    return {"data": {"values": values}}


def create_or_update_person(payload: dict, email: str) -> tuple[str, str]:
    """Returns (status, record_id_or_error)."""
    existing_id = search_person_by_email(email) if email else None

    if existing_id:
        # Update only the vertical tag — leave other fields alone
        update_payload = {
            "data": {
                "values": {
                    "vertical": payload["data"]["values"].get("vertical"),
                    "description": payload["data"]["values"].get("description"),
                }
            }
        }
        try:
            r = requests.patch(
                f"{ATTIO_BASE}/objects/people/records/{existing_id}",
                headers=attio_headers(),
                json=update_payload,
                timeout=15,
            )
            if r.status_code in (200, 201):
                return ("updated", existing_id)
            return ("update_error", f"{r.status_code}: {r.text[:200]}")
        except Exception as e:
            return ("update_error", str(e))

    # Create new
    try:
        r = requests.post(
            f"{ATTIO_BASE}/objects/people/records",
            headers=attio_headers(),
            json=payload,
            timeout=15,
        )
        if r.status_code in (200, 201):
            data = r.json().get("data", {})
            rid = data.get("id", {})
            record_id = rid.get("record_id") if isinstance(rid, dict) else ""
            return ("created", record_id)
        return ("create_error", f"{r.status_code}: {r.text[:200]}")
    except Exception as e:
        return ("create_error", str(e))


def process_file(vertical: str, filename: str, dry_run: bool) -> dict:
    path = DATA_DIR / filename
    if not path.exists():
        print(f"  SKIP — file not found: {filename}")
        return {"vertical": vertical, "status": "missing"}

    with open(path) as f:
        rows = list(csv.DictReader(f))

    print(f"\n=== {vertical} — {filename} — {len(rows)} rows ===")

    stats = {
        "vertical": vertical,
        "total": len(rows),
        "created": 0,
        "updated": 0,
        "errors": 0,
        "error_samples": [],
    }

    for i, row in enumerate(rows):
        payload = build_payload(row, vertical)
        email = (row.get("email") or "").strip().lower()
        name = (row.get("full_name") or "").strip()

        if dry_run:
            if i < 3:
                print(f"  [{i+1}] {name} <{email}>")
                print(f"      payload: {json.dumps(payload['data']['values'], indent=8)[:400]}")
            continue

        status, detail = create_or_update_person(payload, email)
        if status == "created":
            stats["created"] += 1
            print(f"  ✓ [{i+1}/{len(rows)}] CREATED {name} <{email}>")
        elif status == "updated":
            stats["updated"] += 1
            print(f"  ↻ [{i+1}/{len(rows)}] UPDATED {name} <{email}> (already in Attio)")
        else:
            stats["errors"] += 1
            if len(stats["error_samples"]) < 3:
                stats["error_samples"].append(f"{name}: {detail[:200]}")
            print(f"  ✗ [{i+1}/{len(rows)}] {status.upper()} {name}: {detail[:120]}")

        # Be polite to the Attio API
        time.sleep(0.15)

    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Show first 3 per file, no writes")
    args = parser.parse_args()

    print(f"AVO OwnerPhones → Attio importer")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE WRITE'}")
    print(f"Source: {DATA_DIR}")
    print(f"Token: {'present' if os.getenv('ATTIO_API_KEY') else 'MISSING'}")

    all_stats = []
    for vertical, filename in FILE_VERTICAL_MAP.items():
        stats = process_file(vertical, filename, args.dry_run)
        all_stats.append(stats)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for s in all_stats:
        if s.get("status") == "missing":
            print(f"  {s['vertical']:15s} MISSING")
            continue
        print(f"  {s['vertical']:15s} total={s['total']} created={s['created']} updated={s['updated']} errors={s['errors']}")
        for sample in s.get("error_samples", []):
            print(f"      err: {sample[:120]}")

    total_created = sum(s.get("created", 0) for s in all_stats)
    total_updated = sum(s.get("updated", 0) for s in all_stats)
    total_errors = sum(s.get("errors", 0) for s in all_stats)
    print(f"\n  TOTAL: {total_created} created · {total_updated} updated · {total_errors} errors")


if __name__ == "__main__":
    main()
