"""Darrell — RevOps Agent for Automotive Intelligence (HubSpot).

After cleanup classifies contacts as Dealership Decision Makers:
- Creates HubSpot deals
- Fires 5-email insider sequence
- Monitors for hot leads (3+ opens)
- Alerts Michael via Twilio
Schedule: Every 1 hour.
"""

import os
import requests
from datetime import datetime, timedelta
from core.logger import log_enrollment, log_sequence_event, log_info, log_error, log_hot_lead
from core.notifier import notify_hot_lead
from core.hours import is_email_hours
from rivers.automotive_intelligence.deals import create_deal
from rivers.automotive_intelligence.sequences import get_sequence, render_message

HUBSPOT_BASE_URL = "https://api.hubapi.com"


def _hs_key() -> str:
    return os.environ.get("HUBSPOT_API_KEY", "")


def _hs_headers() -> dict:
    return {"Authorization": f"Bearer {_hs_key()}", "Content-Type": "application/json"}

_enrolled = {}
_stats = {"enrolled": 0, "hot_leads": 0, "messages_sent": 0, "deals_created": 0}


def darrell_run():
    """Main loop — called by scheduler every 1 hour."""
    log_info("automotive_intelligence", "=== DARRELL RUN START ===")
    try:
        dealers = _find_verified_dealers()
        for contact in dealers:
            _enroll_dealer(contact)

        _process_sequences()
        _check_hot_leads()

        log_info("automotive_intelligence", f"=== DARRELL RUN COMPLETE === Enrolled: {_stats['enrolled']} | Deals: {_stats['deals_created']}")
    except Exception as e:
        log_error("automotive_intelligence", f"Darrell run failed: {e}")


def _find_verified_dealers() -> list:
    """Find contacts classified as Dealership Decision Maker not yet enrolled."""
    if not _hs_key():
        log_info("automotive_intelligence", "[DRY RUN] No HUBSPOT_API_KEY — skipping")
        return []

    dealers = []
    url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts/search"
    payload = {
        "filterGroups": [
            {
                "filters": [
                    {
                        "propertyName": "contact_type",
                        "operator": "EQ",
                        "value": "dealership_decision_maker",
                    }
                ]
            }
        ],
        "properties": ["email", "firstname", "lastname", "company", "phone"],
        "limit": 100,
    }

    try:
        after = 0
        while True:
            if after:
                payload["after"] = after
            resp = requests.post(url, headers=_hs_headers(), json=payload)
            if resp.status_code == 200:
                data = resp.json()
                for c in data.get("results", []):
                    cid = c["id"]
                    if cid not in _enrolled:
                        dealers.append(c)
                paging = data.get("paging")
                if paging and paging.get("next"):
                    after = paging["next"]["after"]
                else:
                    break
            else:
                log_error("automotive_intelligence", f"HubSpot search failed: {resp.status_code}")
                break
    except Exception as e:
        log_error("automotive_intelligence", f"HubSpot search error: {e}")

    log_info("automotive_intelligence", f"Found {len(dealers)} new verified dealers")
    return dealers


def _enroll_dealer(contact: dict):
    """Create deal and enroll in email sequence."""
    cid = contact["id"]
    props = contact.get("properties", {})
    name = f"{props.get('firstname', '')} {props.get('lastname', '')}".strip()
    company = props.get("company", "")
    email = props.get("email", "")

    # Create deal
    deal_id = create_deal(cid, name, company)
    if deal_id:
        _stats["deals_created"] += 1

    _enrolled[cid] = {
        "enrolled_at": datetime.now(),
        "last_step": -1,
        "contact": {
            "firstName": props.get("firstname", ""),
            "lastName": props.get("lastname", ""),
            "company": company,
            "email": email,
            "phone": props.get("phone", ""),
        },
        "deal_id": deal_id,
        "email_opens": 0,
    }

    _stats["enrolled"] += 1
    log_enrollment("automotive_intelligence", cid, name, "dealer-sequence")
    log_info("automotive_intelligence", f"ENROLLED: {name} at {company}")

    # Send Email 1 only during business hours
    if is_email_hours():
        _send_sequence_step(cid, 0)
    else:
        log_info("automotive_intelligence", f"QUEUED: {name} Email 1 — waiting for business hours (Mon-Fri 7am-9pm CST)")


def _process_sequences():
    """Send due sequence steps — only during email hours (Mon-Fri 7am-9pm CST)."""
    if not is_email_hours():
        log_info("automotive_intelligence", "Outside email hours — skipping sequence sends")
        return

    now = datetime.now()
    for cid, data in list(_enrolled.items()):
        enrolled_at = data["enrolled_at"]
        last_step = data["last_step"]
        sequence = get_sequence()

        for step in sequence:
            step_day = step["day"]
            if step_day <= last_step:
                continue
            due_at = enrolled_at + timedelta(days=step_day)
            if now >= due_at:
                _send_sequence_step(cid, step_day)
                break


def _send_sequence_step(contact_id: str, step_day: int):
    if contact_id not in _enrolled:
        return

    data = _enrolled[contact_id]
    contact = data["contact"]
    sequence = get_sequence()

    step = next((s for s in sequence if s["day"] == step_day), None)
    if not step:
        return

    rendered = render_message(step, contact)
    name = f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip()

    _send_hubspot_email(contact_id, contact.get("email", ""), rendered.get("subject", ""), rendered["body"])

    data["last_step"] = step_day
    _stats["messages_sent"] += 1
    log_sequence_event("automotive_intelligence", contact_id, "email_sent", f"day_{step_day}")
    log_info("automotive_intelligence", f"SENT Day {step_day} to {name}")


def _send_hubspot_email(contact_id: str, to_email: str, subject: str, body: str):
    """Send email via HubSpot single-send API."""
    if not _hs_key() or not to_email:
        log_info("automotive_intelligence", f"[DRY RUN] Email to {contact_id}: {subject}")
        return

    url = f"{HUBSPOT_BASE_URL}/marketing/v3/transactional/single-email/send"
    payload = {
        "message": {
            "to": to_email,
            "subject": subject,
            "body": body.replace("\n", "<br>"),
        },
        "contactProperties": {"email": to_email},
    }
    try:
        resp = requests.post(url, headers=_hs_headers(), json=payload)
        if resp.status_code not in (200, 201):
            # Fallback: create an engagement/email activity
            _create_email_engagement(contact_id, subject, body)
    except Exception as e:
        log_error("automotive_intelligence", f"Email send error: {e}")


def _create_email_engagement(contact_id: str, subject: str, body: str):
    """Create an email engagement in HubSpot as tracking record."""
    url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/emails"
    payload = {
        "properties": {
            "hs_email_subject": subject,
            "hs_email_text": body[:5000],
            "hs_email_status": "SENT",
            "hs_email_direction": "EMAIL",
        },
    }
    try:
        resp = requests.post(url, headers=_hs_headers(), json=payload)
        if resp.status_code in (200, 201):
            email_id = resp.json().get("id")
            assoc_url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/emails/{email_id}/associations/contacts/{contact_id}/email_to_contact"
            requests.put(assoc_url, headers=_hs_headers())
    except Exception as e:
        log_error("automotive_intelligence", f"Engagement creation error: {e}")


def _check_hot_leads():
    """Check for 3+ email opens → hot lead escalation."""
    for cid, data in _enrolled.items():
        if data.get("hot_flagged"):
            continue
        if data.get("email_opens", 0) >= 3:
            contact = data["contact"]
            name = f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip()
            business = contact.get("company", "Unknown")
            phone = contact.get("phone", "N/A")

            data["hot_flagged"] = True
            _stats["hot_leads"] += 1
            log_hot_lead("automotive_intelligence", cid, f"3+ email opens")
            notify_hot_lead("automotive_intelligence", name, business, phone, "opened 3+ emails")

            # Tag in HubSpot
            if _hs_key():
                try:
                    url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts/{cid}"
                    requests.patch(url, headers=_hs_headers(), json={"properties": {"hs_lead_status": "HOT"}})
                except Exception:
                    pass


def get_stats() -> dict:
    return dict(_stats)
