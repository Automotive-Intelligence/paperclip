"""Randy — RevOps Agent for AI Phone Guy (GoHighLevel).

Monitors GHL for tyler-prospect-* tags.
Auto-enrolls contacts immediately on tag.
Fires ICP-specific 12-day sequence.
Checks for hot leads every run.
Schedule: Every 4 hours.
"""

import os
import requests
from datetime import datetime, timedelta
from core.logger import log_enrollment, log_sequence_event, log_info, log_error
from core.hours import is_within_send_window
from rivers.ai_phone_guy.sequences import TAG_TO_VERTICAL, get_sequence, render_message, BOOKING_LINK, SEND_SCHEDULE
from rivers.ai_phone_guy.hot_leads import check_for_hot_leads

GHL_API_KEY = os.environ.get("GHL_API_KEY")
GHL_LOCATION_ID = os.environ.get("GHL_LOCATION_ID")
GHL_BASE = "https://services.leadconnectorhq.com"
GHL_HEADERS = {"Authorization": f"Bearer {GHL_API_KEY}", "Version": "2021-07-28", "Content-Type": "application/json"}

# In-memory tracking (persists while process runs)
_enrolled = {}  # contact_id -> {vertical, enrolled_at, last_step}
_stats = {"enrolled": 0, "hot_leads": 0, "messages_sent": 0}


def randy_run():
    """Main loop — called by scheduler every 4 hours."""
    log_info("ai_phone_guy", "=== RANDY RUN START ===")
    try:
        # Step 1: Find new contacts with tyler-prospect-* tags
        new_contacts = _find_new_prospects()
        for contact in new_contacts:
            _enroll_contact(contact)

        # Step 2: Process sequence steps for enrolled contacts
        _process_sequences()

        # Step 3: Check for hot leads
        enrolled_contacts = _fetch_enrolled_contacts()
        check_for_hot_leads(enrolled_contacts)

        log_info("ai_phone_guy", f"=== RANDY RUN COMPLETE === Enrolled: {_stats['enrolled']} | Messages: {_stats['messages_sent']}")
    except Exception as e:
        log_error("ai_phone_guy", f"Randy run failed: {e}")


def _find_new_prospects() -> list:
    """Find contacts with tyler-prospect-* tags that aren't yet enrolled."""
    if not GHL_API_KEY:
        log_info("ai_phone_guy", "[DRY RUN] No GHL_API_KEY — skipping prospect scan")
        return []

    new_contacts = []
    for tag, vertical in TAG_TO_VERTICAL.items():
        url = f"{GHL_BASE}/contacts/"
        params = {"locationId": GHL_LOCATION_ID, "query": tag, "limit": 100}
        try:
            resp = requests.get(url, headers=GHL_HEADERS, params=params)
            if resp.status_code == 200:
                contacts = resp.json().get("contacts", [])
                for c in contacts:
                    cid = c.get("id")
                    tags = c.get("tags", [])
                    if tag in tags and "sequence-active" not in tags and cid not in _enrolled:
                        c["_vertical"] = vertical
                        new_contacts.append(c)
            else:
                log_error("ai_phone_guy", f"GHL search failed for {tag}: {resp.status_code}")
        except Exception as e:
            log_error("ai_phone_guy", f"GHL search error for {tag}: {e}")

    log_info("ai_phone_guy", f"Found {len(new_contacts)} new prospects")
    return new_contacts


def _enroll_contact(contact: dict):
    """Immediately enroll a contact into their ICP sequence."""
    cid = contact.get("id")
    vertical = contact.get("_vertical")
    name = f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip()

    _enrolled[cid] = {
        "vertical": vertical,
        "enrolled_at": datetime.now(),
        "last_step": -1,
        "contact": contact,
    }

    # Add sequence-active tag
    if GHL_API_KEY:
        url = f"{GHL_BASE}/contacts/{cid}"
        current_tags = contact.get("tags", [])
        current_tags.append("sequence-active")
        requests.put(url, headers=GHL_HEADERS, json={"tags": current_tags})

    _stats["enrolled"] += 1
    log_enrollment("ai_phone_guy", cid, name, f"sequence-{vertical}")
    log_info("ai_phone_guy", f"ENROLLED: {name} → {vertical} sequence")

    # Queue Day 0 message — will send when the vertical's send window opens
    schedule = SEND_SCHEDULE.get(vertical, {})
    if is_within_send_window(schedule):
        _send_sequence_step(cid, 0)
    else:
        log_info("ai_phone_guy", f"QUEUED: {name} Day 0 — waiting for {vertical} send window ({schedule.get('day', '?')} {schedule.get('hour', '?')}:{schedule.get('minute', 0):02d} CST)")


def _process_sequences():
    """Check all enrolled contacts and send due sequence steps.
    Only sends if within the vertical's scheduled send window.
    """
    now = datetime.now()
    for cid, data in list(_enrolled.items()):
        enrolled_at = data["enrolled_at"]
        last_step = data["last_step"]
        vertical = data["vertical"]
        sequence = get_sequence(vertical)

        # Check send window for this vertical
        schedule = SEND_SCHEDULE.get(vertical, {})
        if not is_within_send_window(schedule):
            continue

        for step in sequence:
            step_day = step["day"]
            if step_day <= last_step:
                continue
            due_at = enrolled_at + timedelta(days=step_day)
            if now >= due_at:
                _send_sequence_step(cid, step_day)
                break  # One step at a time per run


def _send_sequence_step(contact_id: str, step_day: int):
    """Send a specific sequence step to a contact."""
    if contact_id not in _enrolled:
        return

    data = _enrolled[contact_id]
    vertical = data["vertical"]
    contact = data["contact"]
    sequence = get_sequence(vertical)

    step = next((s for s in sequence if s["day"] == step_day), None)
    if not step:
        return

    rendered = render_message(step, {
        "firstName": contact.get("firstName", ""),
        "businessName": contact.get("companyName", ""),
    })

    channel = rendered.get("channel", "sms")
    name = f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip()

    if channel == "sms":
        _send_ghl_sms(contact_id, rendered["body"])
    elif channel == "email":
        _send_ghl_email(contact_id, rendered.get("subject", ""), rendered["body"])

    data["last_step"] = step_day
    _stats["messages_sent"] += 1
    log_sequence_event("ai_phone_guy", contact_id, f"{channel}_sent", f"day_{step_day}")
    log_info("ai_phone_guy", f"SENT Day {step_day} {channel} to {name} ({vertical})")


def _send_ghl_sms(contact_id: str, body: str):
    if not GHL_API_KEY:
        log_info("ai_phone_guy", f"[DRY RUN] SMS to {contact_id}: {body[:80]}...")
        return
    url = f"{GHL_BASE}/conversations/messages"
    payload = {
        "type": "SMS",
        "contactId": contact_id,
        "message": body,
    }
    try:
        resp = requests.post(url, headers=GHL_HEADERS, json=payload)
        if resp.status_code not in (200, 201):
            log_error("ai_phone_guy", f"SMS send failed: {resp.status_code} {resp.text}")
    except Exception as e:
        log_error("ai_phone_guy", f"SMS send error: {e}")


def _send_ghl_email(contact_id: str, subject: str, body: str):
    if not GHL_API_KEY:
        log_info("ai_phone_guy", f"[DRY RUN] Email to {contact_id}: {subject}")
        return
    url = f"{GHL_BASE}/conversations/messages"
    payload = {
        "type": "Email",
        "contactId": contact_id,
        "subject": subject,
        "message": body,
        "html": body.replace("\n", "<br>"),
    }
    try:
        resp = requests.post(url, headers=GHL_HEADERS, json=payload)
        if resp.status_code not in (200, 201):
            log_error("ai_phone_guy", f"Email send failed: {resp.status_code} {resp.text}")
    except Exception as e:
        log_error("ai_phone_guy", f"Email send error: {e}")


def get_stats() -> dict:
    return dict(_stats)
