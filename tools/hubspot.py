"""
tools/hubspot.py — HubSpot CRM Integration for Paperclip
Used by Automotive Intelligence (Ryan Data) for contact creation,
email tracking, deal management, and pipeline tracking.
"""

import os
import logging
import requests
from typing import Optional

HUBSPOT_BASE_URL = "https://api.hubapi.com"


def _get_headers() -> dict:
    """Fresh headers on each call in case env vars load after module import."""
    return {
        "Authorization": f"Bearer {os.getenv('HUBSPOT_ACCESS_TOKEN', '')}",
        "Content-Type": "application/json",
    }


def _hubspot_ready() -> bool:
    """Check if HubSpot credentials are configured."""
    return bool(os.getenv("HUBSPOT_ACCESS_TOKEN"))


# ── Contact Management ───────────────────────────────────────────────────────


def search_contact(email: Optional[str] = None, name: Optional[str] = None) -> Optional[dict]:
    """Search for an existing contact by email or company name to avoid duplicates."""
    if not email and not name:
        return None

    try:
        filters = []
        if email:
            filters.append({
                "propertyName": "email",
                "operator": "EQ",
                "value": email,
            })
        elif name:
            filters.append({
                "propertyName": "company",
                "operator": "CONTAINS_TOKEN",
                "value": name,
            })

        payload = {
            "filterGroups": [{"filters": filters}],
            "properties": ["email", "company", "firstname", "lastname", "hs_lead_status"],
            "limit": 1,
        }

        resp = requests.post(
            f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts/search",
            headers=_get_headers(),
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return results[0] if results else None
    except Exception as e:
        logging.warning(f"[HubSpot] Contact search failed: {e}")
        return None


def create_contact(
    business_name: str,
    city: str,
    business_type: str,
    email_hook: str,
    reason: str,
    source_agent: str = "ryan_data",
    email: Optional[str] = None,
    phone: Optional[str] = None,
) -> dict:
    """
    Create a contact in HubSpot from Ryan Data's prospect output.
    Returns the created contact dict or raises on failure.
    """
    properties = {
        "company": business_name,
        "city": city,
        "hs_lead_status": "NEW",
        "lifecyclestage": "lead",
        "jobtitle": business_type,
        "hs_content_membership_notes": (
            f"Source: {source_agent} AI Prospecting\n"
            f"Reason: {reason}\n"
            f"Email Hook: {email_hook}\n"
            f"Channel: Cold Email"
        ),
    }

    if email:
        properties["email"] = email
    if phone:
        properties["phone"] = phone

    # Use business_name as firstname if no individual name
    properties["firstname"] = business_name

    resp = requests.post(
        f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts",
        headers=_get_headers(),
        json={"properties": properties},
        timeout=15,
    )
    resp.raise_for_status()
    contact = resp.json()
    logging.info(f"[HubSpot] Created contact: {business_name} ({city}) — ID: {contact.get('id')}")
    return contact


def add_contact_note(contact_id: str, note: str) -> dict:
    """Create an engagement note associated with a HubSpot contact."""
    # Create the note
    note_payload = {
        "properties": {
            "hs_note_body": note,
            "hs_timestamp": str(int(__import__("time").time() * 1000)),
        },
        "associations": [
            {
                "to": {"id": contact_id},
                "types": [
                    {
                        "associationCategory": "HUBSPOT_DEFINED",
                        "associationTypeId": 202,  # note-to-contact
                    }
                ],
            }
        ],
    }

    resp = requests.post(
        f"{HUBSPOT_BASE_URL}/crm/v3/objects/notes",
        headers=_get_headers(),
        json=note_payload,
        timeout=15,
    )
    resp.raise_for_status()
    result = resp.json()
    logging.info(f"[HubSpot] Note added to contact {contact_id}")
    return result


# ── Deal / Pipeline Management ───────────────────────────────────────────────


def create_deal(
    contact_id: str,
    name: str,
    amount: float = 0,
    pipeline: Optional[str] = None,
    stage: Optional[str] = None,
    source_agent: str = "ryan_data",
) -> dict:
    """
    Create a deal in HubSpot linked to a contact.
    pipeline and stage default to env vars if not provided.
    """
    pipeline_id = pipeline or os.getenv("HUBSPOT_PIPELINE_ID", "default")
    stage_id = stage or os.getenv("HUBSPOT_DEAL_STAGE", "appointmentscheduled")

    deal_payload = {
        "properties": {
            "dealname": name,
            "pipeline": pipeline_id,
            "dealstage": stage_id,
            "amount": str(amount),
            "description": f"Created by {source_agent} AI Prospecting",
        },
    }

    resp = requests.post(
        f"{HUBSPOT_BASE_URL}/crm/v3/objects/deals",
        headers=_get_headers(),
        json=deal_payload,
        timeout=15,
    )
    resp.raise_for_status()
    deal = resp.json()
    deal_id = deal.get("id")

    # Associate deal with contact
    if deal_id and contact_id:
        try:
            requests.put(
                f"{HUBSPOT_BASE_URL}/crm/v3/objects/deals/{deal_id}/associations/contacts/{contact_id}/deal_to_contact",
                headers=_get_headers(),
                timeout=15,
            )
        except Exception as e:
            logging.warning(f"[HubSpot] Deal-contact association failed: {e}")

    logging.info(f"[HubSpot] Created deal: {name} — ${amount}")
    return deal


# ── Master Push Function ─────────────────────────────────────────────────────


def push_prospects_to_hubspot(
    prospects: list,
    source_agent: str = "ryan_data",
    business_key: str = "autointelligence",
) -> list:
    """
    Master function — takes Ryan Data's parsed prospect list and pushes all to HubSpot.
    Creates contacts, adds notes, and creates deals.

    Each prospect dict should have:
        business_name, city, business_type, reason, email_hook
        Optional: email, phone, subject, body, follow_up_subject, follow_up_body, monetary_value
    """
    results = []
    deal_value = 2500  # Automotive Intelligence AI readiness audit

    for p in prospects:
        try:
            # Check for duplicate before creating
            existing = search_contact(
                email=p.get("email"),
                name=p.get("business_name"),
            )
            if existing:
                logging.info(f"[HubSpot] Skipping duplicate: {p.get('business_name')}")
                results.append({
                    "business_name": p.get("business_name"),
                    "contact_id": existing.get("id"),
                    "status": "duplicate_skipped",
                })
                continue

            contact = create_contact(
                business_name=p.get("business_name", "Unknown"),
                city=p.get("city", ""),
                business_type=p.get("business_type", ""),
                email_hook=p.get("email_hook", ""),
                reason=p.get("reason", ""),
                source_agent=source_agent,
                email=p.get("email"),
                phone=p.get("phone"),
            )
            contact_id = contact.get("id")

            # Add outreach context as a note
            hook = p.get("email_hook", "")
            if contact_id and hook:
                add_contact_note(
                    contact_id,
                    f"<b>{source_agent.title()}'s Cold Email Hook:</b><br>{hook}<br><br>"
                    f"<b>Targeting Reason:</b><br>{p.get('reason', '')}<br><br>"
                    f"Channel: Cold Email (no SMS - no opt-in consent)",
                )

            # Schedule follow-up note if available
            if contact_id and p.get("follow_up_subject") and p.get("follow_up_body"):
                add_contact_note(
                    contact_id,
                    f"<b>SCHEDULED FOLLOW-UP (Touch 2, Day 3):</b><br>"
                    f"Subject: {p['follow_up_subject']}<br><br>{p['follow_up_body']}",
                )

            # Create deal for pipeline tracking
            if contact_id:
                try:
                    create_deal(
                        contact_id=contact_id,
                        name=f"{p.get('business_name', 'Unknown')} - AI Readiness Assessment",
                        amount=deal_value,
                        source_agent=source_agent,
                    )
                except Exception as deal_err:
                    logging.warning(f"[HubSpot] Deal creation failed: {deal_err}")

            results.append({
                "business_name": p.get("business_name"),
                "contact_id": contact_id,
                "status": "created",
                "email_sent": False,  # HubSpot email sending handled separately
            })

        except Exception as e:
            logging.error(f"[HubSpot] Failed to push prospect {p.get('business_name')}: {e}")
            results.append({
                "business_name": p.get("business_name"),
                "status": "failed",
                "error": str(e),
            })

    created = len([r for r in results if r["status"] == "created"])
    logging.info(f"[HubSpot] Pushed {created}/{len(prospects)} prospects")
    return results
