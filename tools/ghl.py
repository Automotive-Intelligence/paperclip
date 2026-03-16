"""
tools/ghl.py â GoHighLevel CRM Integration for Paperclip
Pushes Tyler's daily prospects into GHL as contacts with tags and notes.
"""

import os
import logging
import requests
from typing import Optional

GHL_BASE_URL = "https://services.leadconnectorhq.com"


def _get_headers() -> dict:
    """Fresh headers on each call in case env vars load after module import."""
    return {
        "Authorization": f"Bearer {os.getenv('GHL_API_KEY', '')}",
        "Content-Type": "application/json",
        "Version": "2021-07-28",
    }


def create_contact(
    business_name: str,
    city: str,
    business_type: str,
    sms_hook: str,
    reason: str,
    source_agent: str = "tyler",
    tags: Optional[list] = None,
) -> dict:
    """
    Create a contact in GHL from a Tyler prospect.
    Returns the created contact dict or raises on failure.
    """
    location_id = os.getenv("GHL_LOCATION_ID", "")
    if not location_id:
        raise ValueError("GHL_LOCATION_ID not set in environment variables.")

    if tags is None:
        tags = ["tyler-prospect-plumber", "ai-phone-guy", business_type.lower().replace(" ", "-")]

    payload = {
        "locationId": location_id,
        "name": business_name,
        "companyName": business_name,
        "city": city,
        "source": "Tyler AI Prospecting",
        "tags": tags,
        "customFields": [
            {"key": "business_type", "value": business_type},
            {"key": "outreach_reason", "value": reason},
            {"key": "sms_hook", "value": sms_hook},
            {"key": "source_agent", "value": source_agent},
        ],
    }

    resp = requests.post(
        f"{GHL_BASE_URL}/contacts/",
        headers=_get_headers(),
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    contact = resp.json().get("contact", {})
    logging.info(f"[GHL] Created contact: {business_name} ({city}) â ID: {contact.get('id')}")
    return contact


def add_contact_note(contact_id: str, note: str) -> dict:
    """Add a note to an existing GHL contact."""
    location_id = os.getenv("GHL_LOCATION_ID", "")
    resp = requests.post(
        f"{GHL_BASE_URL}/contacts/{contact_id}/notes",
        headers=_get_headers(),
        json={"body": note, "locationId": location_id},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def push_prospects_to_ghl(prospects: list) -> list:
    """
    Master function â takes Tyler's parsed prospect list and pushes all to GHL.

    Each prospect dict should have:
        business_name, city, business_type, sms_hook, reason

    Returns list of results with contact IDs for logging.
    """
    results = []
    for p in prospects:
        try:
            contact = create_contact(
                business_name=p.get("business_name", "Unknown"),
                city=p.get("city", ""),
                business_type=p.get("business_type", ""),
                sms_hook=p.get("sms_hook", ""),
                reason=p.get("reason", ""),
            )
            contact_id = contact.get("id")

            # Add SMS hook as a note so it's visible in the contact record
            if contact_id and p.get("sms_hook"):
                add_contact_note(
                    contact_id,
                    f"ð± Tyler's SMS Hook:\n{p['sms_hook']}\n\n"
                    f"ð¯ Targeting Reason:\n{p['reason']}",
                )

            results.append({
                "business_name": p.get("business_name"),
                "contact_id": contact_id,
                "status": "created"
            })

        except Exception as e:
            logging.error(f"[GHL] Failed to push prospect {p.get('business_name')}: {e}")
            results.append({
                "business_name": p.get("business_name"),
                "status": "failed",
                "error": str(e)
            })

    created = len([r for r in results if r["status"] == "created"])
    logging.info(f"[GHL] Pushed {created}/{len(prospects)} prospects to GHL.")
    return results
