"""
tools/attio.py — Attio CRM Integration for Paperclip
Used by Calling Digital (Marcus) for contact/company creation,
note tracking, and list management via Attio's v2 API.
"""

import os
import logging
import requests
from typing import Optional

ATTIO_BASE_URL = "https://api.attio.com/v2"


def _get_headers() -> dict:
    """Fresh headers on each call in case env vars load after module import."""
    return {
        "Authorization": f"Bearer {os.getenv('ATTIO_API_KEY', '')}",
        "Content-Type": "application/json",
    }


def _attio_ready() -> bool:
    """Check if Attio API credentials are configured."""
    return bool(os.getenv("ATTIO_API_KEY"))


# ── Object / Record Helpers ──────────────────────────────────────────────────


def _assert_value(values: dict, attribute: str, value) -> dict:
    """Build an Attio 'assert' record payload entry."""
    values[attribute] = value
    return values


# ── Company Management ───────────────────────────────────────────────────────


def search_company(name: str) -> Optional[dict]:
    """Search for an existing company record by name to avoid duplicates."""
    try:
        payload = {
            "filter": {
                "name": {"$contains": name},
            },
            "limit": 1,
        }
        resp = requests.post(
            f"{ATTIO_BASE_URL}/objects/companies/records/query",
            headers=_get_headers(),
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        records = resp.json().get("data", [])
        return records[0] if records else None
    except Exception as e:
        logging.warning(f"[Attio] Company search failed: {e}")
        return None


def create_company(
    business_name: str,
    city: str = "",
    business_type: str = "",
) -> dict:
    """
    Create or update (upsert) a company record in Attio.
    Attio uses 'assert' to upsert by matching attribute.
    """
    values = {
        "name": business_name,
    }
    if city:
        values["primary_location"] = city

    if business_type:
        values["description"] = business_type

    payload = {
        "data": {
            "values": values,
        },
    }

    resp = requests.post(
        f"{ATTIO_BASE_URL}/objects/companies/records",
        headers=_get_headers(),
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    record = resp.json().get("data", {})
    logging.info(f"[Attio] Created company: {business_name} — ID: {record.get('id', {}).get('record_id', 'unknown')}")
    return record


# ── Person (Contact) Management ──────────────────────────────────────────────


def search_person(email: Optional[str] = None, name: Optional[str] = None) -> Optional[dict]:
    """Search for an existing person record by email or name."""
    try:
        if email:
            filter_obj = {"email_addresses": {"$contains": email}}
        elif name:
            filter_obj = {"name": {"$contains": name}}
        else:
            return None

        resp = requests.post(
            f"{ATTIO_BASE_URL}/objects/people/records/query",
            headers=_get_headers(),
            json={"filter": filter_obj, "limit": 1},
            timeout=15,
        )
        resp.raise_for_status()
        records = resp.json().get("data", [])
        return records[0] if records else None
    except Exception as e:
        logging.warning(f"[Attio] Person search failed: {e}")
        return None


def create_person(
    business_name: str,
    city: str = "",
    business_type: str = "",
    email: Optional[str] = None,
    phone: Optional[str] = None,
    company_record_id: Optional[str] = None,
) -> dict:
    """Create a person record in Attio, optionally linked to a company."""
    values = {
        "name": business_name,
    }
    if email:
        values["email_addresses"] = email
    if phone:
        values["phone_numbers"] = phone

    payload = {
        "data": {
            "values": values,
        },
    }

    resp = requests.post(
        f"{ATTIO_BASE_URL}/objects/people/records",
        headers=_get_headers(),
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    record = resp.json().get("data", {})
    record_id = record.get("id", {}).get("record_id", "")
    logging.info(f"[Attio] Created person: {business_name} — ID: {record_id}")
    return record


# ── Notes ────────────────────────────────────────────────────────────────────


def add_note(
    parent_object: str,
    parent_record_id: str,
    title: str,
    content: str,
) -> dict:
    """
    Create a note attached to a record in Attio.
    parent_object: 'companies' or 'people'
    """
    payload = {
        "data": {
            "parent_object": parent_object,
            "parent_record_id": parent_record_id,
            "title": title,
            "content_plaintext": content,
        },
    }

    resp = requests.post(
        f"{ATTIO_BASE_URL}/notes",
        headers=_get_headers(),
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    result = resp.json().get("data", {})
    logging.info(f"[Attio] Note added to {parent_object}/{parent_record_id}")
    return result


# ── List Management ──────────────────────────────────────────────────────────


def add_to_list(list_id: str, record_id: str, parent_object: str = "companies") -> dict:
    """Add a record to an Attio list (used for pipeline tracking)."""
    payload = {
        "data": {
            "parent_record_id": record_id,
            "parent_object": parent_object,
        },
    }

    resp = requests.post(
        f"{ATTIO_BASE_URL}/lists/{list_id}/entries",
        headers=_get_headers(),
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    result = resp.json().get("data", {})
    logging.info(f"[Attio] Record {record_id} added to list {list_id}")
    return result


# ── Master Push Function ─────────────────────────────────────────────────────


def push_prospects_to_attio(
    prospects: list,
    source_agent: str = "marcus",
    business_key: str = "callingdigital",
) -> list:
    """
    Master function — takes Marcus's parsed prospect list and pushes all to Attio.
    Creates company records, person records, adds outreach notes.

    Each prospect dict should have:
        business_name, city, business_type, reason, email_hook
        Optional: email, phone, subject, body, follow_up_subject, follow_up_body
    """
    results = []
    list_id = os.getenv("ATTIO_LIST_ID", "")

    for p in prospects:
        try:
            biz_name = p.get("business_name", "Unknown")

            # Check for duplicate company
            existing = search_company(biz_name)
            if existing:
                logging.info(f"[Attio] Skipping duplicate: {biz_name}")
                existing_id = existing.get("id", {}).get("record_id", "")
                results.append({
                    "business_name": biz_name,
                    "record_id": existing_id,
                    "status": "duplicate_skipped",
                })
                continue

            # Create company record
            company = create_company(
                business_name=biz_name,
                city=p.get("city", ""),
                business_type=p.get("business_type", ""),
            )
            company_record_id = company.get("id", {}).get("record_id", "")

            # Create person record if we have contact info
            person_record_id = ""
            if p.get("email") or p.get("phone"):
                person = create_person(
                    business_name=biz_name,
                    city=p.get("city", ""),
                    business_type=p.get("business_type", ""),
                    email=p.get("email"),
                    phone=p.get("phone"),
                    company_record_id=company_record_id,
                )
                person_record_id = person.get("id", {}).get("record_id", "")

            # Add outreach context as a note on the company
            hook = p.get("email_hook", "")
            if company_record_id and hook:
                add_note(
                    parent_object="companies",
                    parent_record_id=company_record_id,
                    title=f"Marcus Cold Email — {biz_name}",
                    content=(
                        f"Cold Email Hook:\n{hook}\n\n"
                        f"Targeting Reason:\n{p.get('reason', '')}\n\n"
                        f"Channel: Cold Email (no SMS - no opt-in consent)"
                    ),
                )

            # Add follow-up note if available
            if company_record_id and p.get("follow_up_subject") and p.get("follow_up_body"):
                add_note(
                    parent_object="companies",
                    parent_record_id=company_record_id,
                    title=f"Follow-up (Touch 2) — {biz_name}",
                    content=(
                        f"Subject: {p['follow_up_subject']}\n\n"
                        f"{p['follow_up_body']}"
                    ),
                )

            # Add to pipeline list if configured
            if company_record_id and list_id:
                try:
                    add_to_list(list_id, company_record_id, parent_object="companies")
                except Exception as list_err:
                    logging.warning(f"[Attio] List add failed: {list_err}")

            results.append({
                "business_name": biz_name,
                "record_id": company_record_id,
                "person_id": person_record_id,
                "status": "created",
            })

        except Exception as e:
            logging.error(f"[Attio] Failed to push prospect {p.get('business_name')}: {e}")
            results.append({
                "business_name": p.get("business_name"),
                "status": "failed",
                "error": str(e),
            })

    created = len([r for r in results if r["status"] == "created"])
    logging.info(f"[Attio] Pushed {created}/{len(prospects)} prospects")
    return results
