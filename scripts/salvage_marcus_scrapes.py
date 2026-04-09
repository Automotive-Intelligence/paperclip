# AVO — AI Business Operating System
# Marcus Salvage Importer
# Reads the 2026-04-09 backup, filters out junk, re-imports real Dallas SMBs
# tagged with pipeline_stage = "marcus-salvage"
# Salesdroid — April 2026

"""Salvage real Dallas SMBs from Marcus's nuked scrape backup.

Reads:    data/backups/attio_people_backup_20260409_140109.json
Filters:  Removes junk (fake 555 phones, John Doe placeholders, test
          accounts, Attio employees, wrong-river auto industry, you).
Imports:  Re-creates each real record as a People + linked Company in
          Attio with pipeline_stage = "marcus-salvage" so you can review
          them later in the UI.

Idempotent — re-running upserts by email.
"""

import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKUP_PATH = REPO_ROOT / "data" / "backups" / "attio_people_backup_20260409_140109.json"
ATTIO_BASE = "https://api.attio.com/v2"

# Things to skip — exact email matches
JUNK_EMAILS = {
    "salesdroid@gmail.com",          # Test record
    "michael@calling.digital",       # Michael himself
    "nico.corbo@attio.com",          # Attio employee
    "tmassey@stoepelford.com",       # Auto industry — wrong river
    "alind@redmac.net",              # Auto industry — wrong river
    "adobe@gc.deliahw.edu.hk",       # Junk
    "tattoosbymartin21@gmail.com",   # No data
    "service@bradfieldpiano.com",    # No description
    "adewalewyze@gmail.com",         # No data
    "harry@ranked.ai",               # SaaS founder, not Calling Digital ICP
    "paulp@teamworkscom.com",        # Enterprise consultant, not Calling Digital ICP
}

# Skip if name contains these (test/placeholder names)
JUNK_NAME_PATTERNS = [
    r"^john doe$",
    r"^jane smith$",
    r"prospect$",   # "Ashleigh Prospect", "Mercado Prospect"
    r"^\?$",
]

# Skip if description contains these (LLM hallucination indicators)
HALLUCINATION_INDICATORS = [
    r"\(214\)\s*555-\d{4}",   # Hollywood fake-phone area code
    r"\(469\)\s*555-\d{4}",
    r"\(972\)\s*555-\d{4}",
]


def attio_token() -> str:
    t = (os.getenv("ATTIO_API_KEY") or "").strip()
    if not t:
        print("ERROR: ATTIO_API_KEY not set")
        sys.exit(1)
    return t


def attio_headers() -> dict:
    return {
        "Authorization": f"Bearer {attio_token()}",
        "Content-Type": "application/json",
    }


def is_junk(person: dict) -> tuple[bool, str]:
    """Decide if this record should be skipped. Returns (skip, reason)."""
    vals = person.get("values", {})

    # Email check
    email_obj = (vals.get("email_addresses") or [{}])[0]
    email = (email_obj.get("email_address", "") or "").lower().strip() if isinstance(email_obj, dict) else ""
    if email in JUNK_EMAILS:
        return True, f"junk email match ({email})"
    if not email:
        return True, "no email"

    # Name check
    name_obj = (vals.get("name") or [{}])[0]
    full_name = (name_obj.get("full_name", "") or "").strip().lower() if isinstance(name_obj, dict) else ""
    for pattern in JUNK_NAME_PATTERNS:
        if re.search(pattern, full_name):
            return True, f"junk name pattern ({full_name})"
    if not full_name or full_name == "?":
        return True, "no name"

    # Description hallucination check
    desc_obj = (vals.get("description") or [{}])[0]
    desc = (desc_obj.get("value", "") or "") if isinstance(desc_obj, dict) else ""
    for pattern in HALLUCINATION_INDICATORS:
        if re.search(pattern, desc):
            return True, "555 phone (LLM hallucination)"

    return False, ""


def parse_marcus_description(desc: str) -> dict:
    """Marcus's description format:
    'Company: <name> | marcus prospecting: <reason> | Phone: ... | Website: ...'
    Returns dict with company, reason, phone, website.
    """
    out = {"company": "", "reason": "", "phone": "", "website": ""}
    if not desc:
        return out
    parts = [p.strip() for p in desc.split("|")]
    for p in parts:
        if p.lower().startswith("company:"):
            out["company"] = p[8:].strip()
        elif p.lower().startswith("marcus prospecting:"):
            out["reason"] = p[19:].strip()
        elif p.lower().startswith("phone:"):
            out["phone"] = p[6:].strip()
        elif p.lower().startswith("website:"):
            out["website"] = p[8:].strip()
    return out


def normalize_domain(raw: str) -> str:
    d = (raw or "").strip().lower()
    if not d:
        return ""
    for prefix in ("https://", "http://", "www."):
        if d.startswith(prefix):
            d = d[len(prefix):]
    d = d.split("/")[0].split("?")[0]
    return d


def normalize_phone(raw: str) -> str:
    digits = "".join(c for c in (raw or "") if c.isdigit())
    if not digits:
        return ""
    if not digits.startswith("1") and len(digits) == 10:
        digits = "1" + digits
    return f"+{digits}"


def search_company_by_domain(domain: str) -> str | None:
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
    except Exception:
        pass
    return None


def upsert_company(company_name: str, domain: str, phone: str, website: str, reason: str) -> str | None:
    """Upsert by domain, return record_id."""
    payload_values = {
        "cd_industry": [{"value": "dallas smb"}],
        "cd_prospect_notes": [{"value": (reason or "")[:500]}],
        "cd_source_agent": [{"value": "marcus-salvage"}],
        "cd_prospected_date": [{"value": date.today().isoformat()}],
        "cd_outreach_stage": [{"value": "salvaged"}],
    }
    if company_name:
        payload_values["name"] = [{"value": company_name}]
    if domain:
        payload_values["domains"] = [{"domain": domain}]

    payload = {"data": {"values": payload_values}}

    existing = search_company_by_domain(domain)
    try:
        if existing:
            r = requests.patch(
                f"{ATTIO_BASE}/objects/companies/records/{existing}",
                headers=attio_headers(), json=payload, timeout=15,
            )
            if r.status_code in (200, 201):
                return existing
        else:
            r = requests.post(
                f"{ATTIO_BASE}/objects/companies/records",
                headers=attio_headers(), json=payload, timeout=15,
            )
            if r.status_code in (200, 201):
                data = r.json().get("data", {})
                rid = data.get("id", {})
                return rid.get("record_id") if isinstance(rid, dict) else ""
        print(f"  company error: {r.status_code} {r.text[:150]}")
    except Exception as e:
        print(f"  company exception: {e}")
    return None


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


def upsert_person(name: str, email: str, phone: str, company_id: str | None, reason: str) -> tuple[str, str]:
    parts = name.split()
    first = parts[0] if parts else "Prospect"
    last = " ".join(parts[1:]) if len(parts) > 1 else "Unknown"

    values = {
        "name": [{
            "first_name": first,
            "last_name": last or "Unknown",
            "full_name": name or f"{first} {last}".strip(),
        }],
        "email_addresses": [{"email_address": email}],
        "pipeline_stage": "marcus-salvage",
        "description": [{"value": f"Marcus salvage import. {reason[:300]}"}],
    }

    if phone:
        values["phone_numbers"] = [{"original_phone_number": phone}]
    if company_id:
        values["company"] = [{
            "target_object": "companies",
            "target_record_id": company_id,
        }]

    payload = {"data": {"values": values}}

    existing = search_person_by_email(email)
    try:
        if existing:
            r = requests.patch(
                f"{ATTIO_BASE}/objects/people/records/{existing}",
                headers=attio_headers(), json=payload, timeout=15,
            )
            if r.status_code in (200, 201):
                return ("updated", existing)
            return ("error", f"patch {r.status_code}: {r.text[:150]}")
        r = requests.post(
            f"{ATTIO_BASE}/objects/people/records",
            headers=attio_headers(), json=payload, timeout=15,
        )
        if r.status_code in (200, 201):
            data = r.json().get("data", {})
            rid = data.get("id", {})
            return ("created", rid.get("record_id") if isinstance(rid, dict) else "")
        return ("error", f"post {r.status_code}: {r.text[:150]}")
    except Exception as e:
        return ("error", str(e))


def main():
    print(f"Reading: {BACKUP_PATH}")
    with open(BACKUP_PATH) as f:
        all_people = json.load(f)
    print(f"Total backed-up records: {len(all_people)}")

    # Only the untagged ones (Marcus's old scrapes)
    untagged = []
    for p in all_people:
        v = p.get("values", {}).get("vertical")
        if not v or (isinstance(v, list) and not v):
            untagged.append(p)
    print(f"Untagged (Marcus scrapes): {len(untagged)}")

    salvageable = []
    skipped = []
    for p in untagged:
        is_j, reason = is_junk(p)
        if is_j:
            skipped.append((p, reason))
        else:
            salvageable.append(p)

    print(f"  Salvageable: {len(salvageable)}")
    print(f"  Skipped:     {len(skipped)}")
    print()

    if skipped:
        print("=== SKIPPED ===")
        for p, reason in skipped:
            name_obj = (p.get("values", {}).get("name") or [{}])[0]
            name = name_obj.get("full_name", "?") if isinstance(name_obj, dict) else "?"
            print(f"  - {name[:35]:35s}  {reason}")
        print()

    print("=== SALVAGING ===")
    created = 0
    updated = 0
    errors = 0
    for i, p in enumerate(salvageable, 1):
        vals = p.get("values", {})
        name_obj = (vals.get("name") or [{}])[0]
        name = name_obj.get("full_name", "") if isinstance(name_obj, dict) else ""
        email_obj = (vals.get("email_addresses") or [{}])[0]
        email = email_obj.get("email_address", "") if isinstance(email_obj, dict) else ""
        desc_obj = (vals.get("description") or [{}])[0]
        desc = desc_obj.get("value", "") if isinstance(desc_obj, dict) else ""

        parsed = parse_marcus_description(desc)
        company_name = parsed["company"]
        reason = parsed["reason"] or desc[:300]
        phone = normalize_phone(parsed["phone"])
        website = parsed["website"]
        domain = normalize_domain(website) or normalize_domain(email.split("@")[-1] if "@" in email else "")

        # Upsert company
        company_id = None
        if company_name or domain:
            company_id = upsert_company(company_name, domain, phone, website, reason)

        # Upsert person
        status, detail = upsert_person(name, email, phone, company_id, reason)
        if status == "created":
            created += 1
            print(f"  ✓ [{i}/{len(salvageable)}] CREATED {name} <{email}> → {company_name or 'no-company'}")
        elif status == "updated":
            updated += 1
            print(f"  ↻ [{i}/{len(salvageable)}] UPDATED {name} <{email}>")
        else:
            errors += 1
            print(f"  ✗ [{i}/{len(salvageable)}] ERROR {name}: {detail[:120]}")

        time.sleep(0.18)

    print()
    print("=" * 60)
    print(f"DONE — created={created} updated={updated} errors={errors}")
    print("=" * 60)


if __name__ == "__main__":
    main()
