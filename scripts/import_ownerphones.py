# AVO — AI Business Operating System
# OwnerPhones CSV → Attio Importer (Companies + People + cd_* fields)
# Salesdroid — April 2026

"""Import OwnerPhones CSVs into Attio with full Company + People linking.

Usage:
    python scripts/import_ownerphones.py --dry-run    # show first 3 per file
    python scripts/import_ownerphones.py              # live import

For each row in each CSV:
  1. Upsert a Company record by domain
       - name, domain, description
       - cd_industry (friendly version of vertical)
       - cd_prospect_notes (personalized one-liner using title + city)
       - cd_source_agent = "ownerphones-import"
       - cd_prospected_date = today
       - cd_outreach_stage = "imported"
  2. Upsert a Person record by email
       - name, email, phone, job_title, linkedin
       - vertical (single-select)
       - description
       - company (linked to the Company from step 1)

Idempotent — re-running updates existing records, never duplicates.
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import date
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

# Friendly industry names for cd_industry — used in sequence merge fields
# {{company.cd_industry}} renders inside the email body so this needs to read naturally
VERTICAL_INDUSTRY_LABEL = {
    "med-spa": "med spa",
    "pi-law": "personal injury law",
    "real-estate": "real estate",
    "home-builder": "custom home building",
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
    """Attio wants E.164. OwnerPhones gives 12103805509 → +12103805509."""
    digits = "".join(c for c in (raw or "") if c.isdigit())
    if not digits:
        return ""
    if not digits.startswith("1") and len(digits) == 10:
        digits = "1" + digits
    return f"+{digits}"


def normalize_domain(raw: str) -> str:
    """Strip protocols, www, paths, lowercase."""
    d = (raw or "").strip().lower()
    if not d:
        return ""
    for prefix in ("https://", "http://", "www."):
        if d.startswith(prefix):
            d = d[len(prefix):]
    d = d.split("/")[0].split("?")[0]
    return d


def build_prospect_note(row: dict, vertical: str) -> str:
    """Personalized one-liner that renders well inside the sequence email.

    Example output:
      "Saw Crosley Law Firm in San Antonio — Tom is the Owner. Reached out
      because most PI firms in Texas are losing leads after-hours and AI
      can fix it without changing how they practice."
    """
    first = (row.get("first_name") or "").strip()
    title = (row.get("title") or "").strip()
    company = (row.get("company") or "").strip()
    locality = (row.get("locality") or "").strip()
    region = (row.get("region") or "").strip()

    # Title-case location to read naturally in the email
    locality_clean = locality.title() if locality else ""
    region_clean = region.strip()
    location = locality_clean
    if locality_clean and region_clean and region_clean.lower() != locality_clean.lower():
        location = f"{locality_clean}, {region_clean}"

    role_clause = ""
    if first and title:
        role_clause = f"{first} is the {title}. "
    elif first:
        role_clause = f"{first} runs the team. "

    location_clause = f"in {location} " if location else ""

    # Vertical-specific hook (the WHY of the outreach)
    hooks = {
        "med-spa": (
            "Most med spas in Texas are missing 30%+ of after-hours inquiries "
            "and Sophie can answer every one without changing how the front desk works."
        ),
        "pi-law": (
            "Most PI firms in Texas lose new clients to voicemail after 5pm "
            "and AI intake can capture every case without changing how the firm practices."
        ),
        "real-estate": (
            "Most Texas agents lose 1-2 deals a month to slow lead response "
            "and an AI front desk can answer instantly while you're showing homes."
        ),
        "home-builder": (
            "Most custom builders in Texas are referral-dependent and don't realize "
            "AI can qualify and nurture inbound leads while you're on jobsites."
        ),
    }
    hook = hooks.get(vertical, "")

    note = f"Saw {company or 'their team'} {location_clause}— {role_clause}{hook}".strip()
    return note


def build_company_payload(row: dict, vertical: str) -> dict:
    """Build the Attio Company record payload."""
    company_name = (row.get("company") or "").strip()
    domain = normalize_domain(row.get("company_domain") or row.get("domain") or "")

    industry_label = VERTICAL_INDUSTRY_LABEL.get(vertical, vertical)
    note = build_prospect_note(row, vertical)

    values = {
        "name": [{"value": company_name}] if company_name else [],
        "cd_industry": [{"value": industry_label}],
        "cd_prospect_notes": [{"value": note}],
        "cd_source_agent": [{"value": "ownerphones-import"}],
        "cd_prospected_date": [{"value": date.today().isoformat()}],
        "cd_outreach_stage": [{"value": "imported"}],
    }

    if domain:
        # Attio Company.domains is a special "domains" attribute
        values["domains"] = [{"domain": domain}]

    return {"data": {"values": values}}


def build_person_payload(row: dict, vertical: str, company_record_id: str | None) -> dict:
    """Build the Attio Person record payload, linked to a Company."""
    first = (row.get("first_name") or "").strip()
    last = (row.get("last_name") or "").strip()
    full = (row.get("full_name") or f"{first} {last}").strip()
    email = (row.get("email") or "").strip().lower()
    phone = normalize_phone(row.get("mobile_phone1") or row.get("phone_number1") or "")
    title = (row.get("title") or "").strip()
    linkedin = (row.get("linkedin") or "").strip()
    company_name = (row.get("company") or "").strip()
    locality = (row.get("locality") or "").strip()
    region = (row.get("region") or "").strip()

    description = (
        f"OwnerPhones import [{vertical}]. "
        f"Title: {title}. Company: {company_name}. "
        f"Location: {locality}, {region}."
    )

    values = {
        "name": [{
            "first_name": first or "Prospect",
            "last_name": last or "Unknown",
            "full_name": full or f"{first} {last}".strip() or "Unknown Prospect",
        }],
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
    if company_record_id:
        # Attio record-reference: pass target object + record_id
        values["company"] = [{
            "target_object": "companies",
            "target_record_id": company_record_id,
        }]

    return {"data": {"values": values}}


# ── Attio API helpers ────────────────────────────────────────────────────────

def search_company_by_domain(domain: str) -> str | None:
    """Return existing Company record_id by domain, or None."""
    if not domain:
        return None
    try:
        r = requests.post(
            f"{ATTIO_BASE}/objects/companies/records/query",
            headers=attio_headers(),
            json={"filter": {"domains": {"domain": {"$eq": domain}}}, "limit": 1},
            timeout=15,
        )
        if r.status_code == 200:
            results = r.json().get("data", [])
            if results:
                rid = results[0].get("id", {})
                return rid.get("record_id") if isinstance(rid, dict) else None
    except Exception as e:
        print(f"  search_company error: {e}")
    return None


def search_company_by_name(name: str) -> str | None:
    """Fallback: find company by name when domain lookup fails."""
    if not name:
        return None
    try:
        r = requests.post(
            f"{ATTIO_BASE}/objects/companies/records/query",
            headers=attio_headers(),
            json={"filter": {"name": {"$eq": name}}, "limit": 1},
            timeout=15,
        )
        if r.status_code == 200:
            results = r.json().get("data", [])
            if results:
                rid = results[0].get("id", {})
                return rid.get("record_id") if isinstance(rid, dict) else None
    except Exception:
        pass
    return None


def upsert_company(row: dict, vertical: str) -> tuple[str, str]:
    """Returns (status, record_id_or_error). Status: created | updated | error."""
    domain = normalize_domain(row.get("company_domain") or row.get("domain") or "")
    company_name = (row.get("company") or "").strip()

    existing_id = search_company_by_domain(domain) or search_company_by_name(company_name)

    payload = build_company_payload(row, vertical)

    if existing_id:
        try:
            r = requests.patch(
                f"{ATTIO_BASE}/objects/companies/records/{existing_id}",
                headers=attio_headers(),
                json=payload,
                timeout=15,
            )
            if r.status_code in (200, 201):
                return ("updated", existing_id)
            return ("error", f"company patch {r.status_code}: {r.text[:200]}")
        except Exception as e:
            return ("error", f"company patch exception: {e}")

    try:
        r = requests.post(
            f"{ATTIO_BASE}/objects/companies/records",
            headers=attio_headers(),
            json=payload,
            timeout=15,
        )
        if r.status_code in (200, 201):
            data = r.json().get("data", {})
            rid = data.get("id", {})
            record_id = rid.get("record_id") if isinstance(rid, dict) else ""
            return ("created", record_id)
        return ("error", f"company create {r.status_code}: {r.text[:200]}")
    except Exception as e:
        return ("error", f"company create exception: {e}")


def search_person_by_email(email: str) -> str | None:
    if not email:
        return None
    try:
        r = requests.post(
            f"{ATTIO_BASE}/objects/people/records/query",
            headers=attio_headers(),
            json={"filter": {"email_addresses": {"email_address": {"$eq": email.lower()}}}, "limit": 1},
            timeout=15,
        )
        if r.status_code == 200:
            results = r.json().get("data", [])
            if results:
                rid = results[0].get("id", {})
                return rid.get("record_id") if isinstance(rid, dict) else None
    except Exception:
        pass
    return None


def upsert_person(row: dict, vertical: str, company_id: str | None) -> tuple[str, str]:
    email = (row.get("email") or "").strip().lower()
    payload = build_person_payload(row, vertical, company_id)

    existing_id = search_person_by_email(email) if email else None

    if existing_id:
        try:
            r = requests.patch(
                f"{ATTIO_BASE}/objects/people/records/{existing_id}",
                headers=attio_headers(),
                json=payload,
                timeout=15,
            )
            if r.status_code in (200, 201):
                return ("updated", existing_id)
            return ("error", f"person patch {r.status_code}: {r.text[:200]}")
        except Exception as e:
            return ("error", f"person patch exception: {e}")

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
        return ("error", f"person create {r.status_code}: {r.text[:200]}")
    except Exception as e:
        return ("error", f"person create exception: {e}")


# ── Driver ───────────────────────────────────────────────────────────────────

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
        "companies_created": 0,
        "companies_updated": 0,
        "companies_errors": 0,
        "people_created": 0,
        "people_updated": 0,
        "people_errors": 0,
        "error_samples": [],
    }

    for i, row in enumerate(rows):
        name = (row.get("full_name") or "").strip()
        email = (row.get("email") or "").strip().lower()
        company_name = (row.get("company") or "").strip()

        if dry_run:
            if i < 3:
                company_payload = build_company_payload(row, vertical)
                person_payload = build_person_payload(row, vertical, company_record_id="<would-be-from-step-1>")
                print(f"\n  [{i+1}] {name} <{email}> @ {company_name}")
                print(f"      Company payload values:")
                print(f"        {json.dumps(company_payload['data']['values'], indent=10)[:600]}")
                print(f"      Person payload values:")
                print(f"        {json.dumps(person_payload['data']['values'], indent=10)[:500]}")
            continue

        # Step 1: upsert Company
        c_status, c_detail = upsert_company(row, vertical)
        if c_status == "created":
            stats["companies_created"] += 1
        elif c_status == "updated":
            stats["companies_updated"] += 1
        else:
            stats["companies_errors"] += 1
            if len(stats["error_samples"]) < 5:
                stats["error_samples"].append(f"{name} (company): {c_detail[:200]}")
            print(f"  ✗ [{i+1}/{len(rows)}] COMPANY {c_status.upper()} {company_name}: {c_detail[:120]}")
            continue

        company_id = c_detail  # On success, c_detail is the record_id

        # Step 2: upsert Person, linked to Company
        p_status, p_detail = upsert_person(row, vertical, company_id)
        if p_status == "created":
            stats["people_created"] += 1
            print(f"  ✓ [{i+1}/{len(rows)}] {name} <{email}> → {company_name} ({c_status[:3]})")
        elif p_status == "updated":
            stats["people_updated"] += 1
            print(f"  ↻ [{i+1}/{len(rows)}] {name} <{email}> → {company_name} (updated)")
        else:
            stats["people_errors"] += 1
            if len(stats["error_samples"]) < 5:
                stats["error_samples"].append(f"{name} (person): {p_detail[:200]}")
            print(f"  ✗ [{i+1}/{len(rows)}] PERSON {p_status.upper()} {name}: {p_detail[:120]}")

        time.sleep(0.20)  # Be polite — 5 req/sec across both endpoints = ~10 actual calls/sec total

    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Show first 3 per file, no writes")
    args = parser.parse_args()

    print(f"AVO OwnerPhones → Attio importer (Companies + People)")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE WRITE'}")
    print(f"Source: {DATA_DIR}")
    print(f"Token: {'present' if os.getenv('ATTIO_API_KEY') else 'MISSING'}")

    all_stats = []
    for vertical, filename in FILE_VERTICAL_MAP.items():
        stats = process_file(vertical, filename, args.dry_run)
        all_stats.append(stats)

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for s in all_stats:
        if s.get("status") == "missing":
            print(f"  {s['vertical']:15s} MISSING")
            continue
        print(
            f"  {s['vertical']:15s} "
            f"companies: created={s['companies_created']} updated={s['companies_updated']} err={s['companies_errors']}  |  "
            f"people: created={s['people_created']} updated={s['people_updated']} err={s['people_errors']}"
        )
        for sample in s.get("error_samples", []):
            print(f"      err: {sample[:140]}")

    tc_c = sum(s.get("companies_created", 0) for s in all_stats)
    tc_u = sum(s.get("companies_updated", 0) for s in all_stats)
    tc_e = sum(s.get("companies_errors", 0) for s in all_stats)
    tp_c = sum(s.get("people_created", 0) for s in all_stats)
    tp_u = sum(s.get("people_updated", 0) for s in all_stats)
    tp_e = sum(s.get("people_errors", 0) for s in all_stats)

    print()
    print(f"  TOTAL Companies: {tc_c} created · {tc_u} updated · {tc_e} errors")
    print(f"  TOTAL People:    {tp_c} created · {tp_u} updated · {tp_e} errors")


if __name__ == "__main__":
    main()
