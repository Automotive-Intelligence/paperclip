"""Hot lead detection and escalation for AI Phone Guy.

Triggers:
- SMS reply from prospect
- 3+ email opens on any message in the sequence

Actions:
- Tag contact as hot-lead in GHL
- Send Twilio SMS to Michael within 5 minutes
- Pause the sequence immediately
"""

import os
import requests
from core.logger import log_hot_lead, log_info, log_error
from core.notifier import notify_hot_lead

GHL_API_KEY = os.environ.get("GHL_API_KEY")
GHL_LOCATION_ID = os.environ.get("GHL_LOCATION_ID")
GHL_BASE = "https://services.leadconnectorhq.com"
GHL_HEADERS = {"Authorization": f"Bearer {GHL_API_KEY}", "Version": "2021-07-28", "Content-Type": "application/json"}


def check_for_hot_leads(contacts: list):
    """Check a list of enrolled contacts for hot lead triggers."""
    for contact in contacts:
        cid = contact.get("id")
        tags = contact.get("tags", [])
        name = f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip()
        business = contact.get("companyName", "Unknown")
        phone = contact.get("phone", "N/A")

        if "hot-lead" in tags:
            continue  # Already flagged

        is_hot = False
        trigger = ""

        # Check for SMS reply
        if contact.get("has_sms_reply"):
            is_hot = True
            trigger = "replied to SMS"

        # Check for 3+ email opens
        email_opens = contact.get("email_opens", 0)
        if email_opens >= 3:
            is_hot = True
            trigger = f"opened {email_opens} emails"

        if is_hot:
            _escalate_hot_lead(cid, name, business, phone, trigger)


def _escalate_hot_lead(contact_id: str, name: str, business: str, phone: str, trigger: str):
    """Tag as hot-lead, alert Michael, pause sequence."""
    log_hot_lead("ai_phone_guy", contact_id, trigger)

    # Tag in GHL
    _add_tag(contact_id, "hot-lead")

    # Remove sequence-active tag to pause
    _remove_tag(contact_id, "sequence-active")

    # Alert Michael
    notify_hot_lead("ai_phone_guy", name, business, phone, trigger)
    log_info("ai_phone_guy", f"HOT LEAD ESCALATED: {name} at {business} — {trigger}")


def _add_tag(contact_id: str, tag: str):
    if not GHL_API_KEY:
        log_info("ai_phone_guy", f"[DRY RUN] Would add tag '{tag}' to {contact_id}")
        return
    url = f"{GHL_BASE}/contacts/{contact_id}"
    resp = requests.put(url, headers=GHL_HEADERS, json={"tags": [tag]})
    if resp.status_code != 200:
        log_error("ai_phone_guy", f"Failed to add tag {tag} to {contact_id}: {resp.status_code}")


def _remove_tag(contact_id: str, tag: str):
    if not GHL_API_KEY:
        log_info("ai_phone_guy", f"[DRY RUN] Would remove tag '{tag}' from {contact_id}")
        return
    url = f"{GHL_BASE}/contacts/{contact_id}"
    # GHL: fetch current tags, remove the one we don't want, update
    resp = requests.get(url, headers=GHL_HEADERS)
    if resp.status_code == 200:
        current_tags = resp.json().get("contact", {}).get("tags", [])
        new_tags = [t for t in current_tags if t != tag]
        requests.put(url, headers=GHL_HEADERS, json={"tags": new_tags})
