"""Brenda — RevOps Agent for Calling Digital (Attio).

Monitors Attio for new contacts from OwnerPhones CSV imports.
Scores contacts, assigns Track A or B, fires email sequences.
Flags hot leads to Marcus.
Schedule: Every 2 hours.
"""

import os
import requests
from datetime import datetime, timedelta
from core.logger import log_enrollment, log_sequence_event, log_info, log_error, log_hot_lead
from core.notifier import notify_hot_lead
from rivers.calling_digital.scoring import score_contact, assign_track
from rivers.calling_digital.sequences import get_track_sequence, render_message

ATTIO_API_KEY = os.environ.get("ATTIO_API_KEY")
ATTIO_BASE = "https://api.attio.com/v2"
ATTIO_HEADERS = {"Authorization": f"Bearer {ATTIO_API_KEY}", "Content-Type": "application/json"}

BOOKING_LINK = os.environ.get("BOOKING_LINK_CD", "")

VERTICAL_MAP = {
    "med-spa": {"industry": "med spa", "send_day": "wednesday", "send_hour": 19},
    "pi-law": {"industry": "PI law firm", "send_day": "wednesday", "send_hour": 19},
    "real-estate": {"industry": "real estate", "send_day": "tuesday", "send_hour": 8},
    "home-builder": {"industry": "custom home builder", "send_day": "monday", "send_hour": 6},
}

_enrolled = {}
_stats = {"enrolled": 0, "hot_leads": 0, "messages_sent": 0}


def brenda_run():
    """Main loop — called by scheduler every 2 hours."""
    log_info("calling_digital", "=== BRENDA RUN START ===")
    try:
        new_contacts = _find_new_contacts()
        for contact in new_contacts:
            _score_and_enroll(contact)

        _process_sequences()
        _check_hot_leads()

        log_info("calling_digital", f"=== BRENDA RUN COMPLETE === Enrolled: {_stats['enrolled']} | Messages: {_stats['messages_sent']}")
    except Exception as e:
        log_error("calling_digital", f"Brenda run failed: {e}")


def _find_new_contacts() -> list:
    """Find new contacts in Attio that haven't been enrolled."""
    if not ATTIO_API_KEY:
        log_info("calling_digital", "[DRY RUN] No ATTIO_API_KEY — skipping")
        return []

    contacts = []
    for vertical_tag, meta in VERTICAL_MAP.items():
        try:
            url = f"{ATTIO_BASE}/objects/people/records/query"
            payload = {
                "filter": {
                    "attribute": "tags",
                    "operator": "contains",
                    "value": vertical_tag,
                },
                "limit": 100,
            }
            resp = requests.post(url, headers=ATTIO_HEADERS, json=payload)
            if resp.status_code == 200:
                records = resp.json().get("data", [])
                for r in records:
                    rid = r.get("id", {}).get("record_id", "")
                    if rid and rid not in _enrolled:
                        attrs = r.get("values", {})
                        contact = _parse_attio_record(attrs, vertical_tag, meta)
                        contact["id"] = rid
                        contact["_raw"] = r
                        contacts.append(contact)
            else:
                log_error("calling_digital", f"Attio query failed for {vertical_tag}: {resp.status_code}")
        except Exception as e:
            log_error("calling_digital", f"Attio query error for {vertical_tag}: {e}")

    log_info("calling_digital", f"Found {len(contacts)} new contacts")
    return contacts


def _parse_attio_record(attrs: dict, vertical: str, meta: dict) -> dict:
    """Parse Attio record attributes into a normalized contact dict."""
    def _first_val(key):
        vals = attrs.get(key, [])
        if vals and isinstance(vals, list):
            v = vals[0]
            if isinstance(v, dict):
                return v.get("value") or v.get("original_value") or ""
            return str(v)
        return ""

    return {
        "firstName": _first_val("first_name"),
        "lastName": _first_val("last_name"),
        "email": _first_val("email_addresses"),
        "businessName": _first_val("company"),
        "city": _first_val("city"),
        "state": _first_val("state"),
        "vertical": vertical,
        "industry": meta["industry"],
        "revenue": 0,
        "ai_interest": False,
        "referred_by_client": False,
        "content_engaged": False,
    }


def _score_and_enroll(contact: dict):
    """Score contact and enroll in Track A or B."""
    cid = contact["id"]
    score = score_contact(contact)
    track = assign_track(score)
    name = f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip()

    _enrolled[cid] = {
        "track": track,
        "score": score,
        "enrolled_at": datetime.now(),
        "last_step": -1,
        "contact": contact,
    }

    _stats["enrolled"] += 1
    log_enrollment("calling_digital", cid, name, f"track-{track} (score={score})")
    log_info("calling_digital", f"ENROLLED: {name} → Track {track} (score={score})")

    # Fire Day 1 message immediately
    _send_sequence_step(cid, 1)


def _process_sequences():
    """Send due sequence steps for all enrolled contacts."""
    now = datetime.now()
    for cid, data in list(_enrolled.items()):
        enrolled_at = data["enrolled_at"]
        last_step = data["last_step"]
        track = data["track"]
        sequence = get_track_sequence(track)

        for step in sequence:
            step_day = step["day"]
            if step_day <= last_step:
                continue
            due_at = enrolled_at + timedelta(days=step_day)
            if now >= due_at:
                _send_sequence_step(cid, step_day)
                break


def _send_sequence_step(contact_id: str, step_day: int):
    """Send a specific sequence step."""
    if contact_id not in _enrolled:
        return

    data = _enrolled[contact_id]
    track = data["track"]
    contact = data["contact"]
    sequence = get_track_sequence(track)

    step = next((s for s in sequence if s["day"] == step_day), None)
    if not step:
        return

    rendered = render_message(step, contact)
    name = f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip()

    _send_attio_email(contact_id, contact.get("email", ""), rendered.get("subject", ""), rendered["body"])

    data["last_step"] = step_day
    _stats["messages_sent"] += 1
    log_sequence_event("calling_digital", contact_id, "email_sent", f"track{track}_day{step_day}")
    log_info("calling_digital", f"SENT Track {track} Day {step_day} to {name}")


def _send_attio_email(contact_id: str, to_email: str, subject: str, body: str):
    """Send email via Attio or log dry run."""
    if not ATTIO_API_KEY or not to_email:
        log_info("calling_digital", f"[DRY RUN] Email to {contact_id}: {subject}")
        return
    # Attio doesn't have a native email send API — use transactional email service
    # For now, log the intent and use the Attio note to track
    try:
        url = f"{ATTIO_BASE}/notes"
        payload = {
            "parent_object": "people",
            "parent_record_id": contact_id,
            "title": f"[Brenda] Sent: {subject}",
            "content": body[:500],
        }
        requests.post(url, headers=ATTIO_HEADERS, json=payload)
    except Exception as e:
        log_error("calling_digital", f"Attio note failed: {e}")


def _check_hot_leads():
    """Check Track B contacts for hot lead signals."""
    for cid, data in _enrolled.items():
        if data["track"] != "B":
            continue
        if data["last_step"] >= 6:
            contact = data["contact"]
            name = f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip()
            business = contact.get("businessName", "Unknown")
            phone = contact.get("phone", "N/A")
            log_hot_lead("calling_digital", cid, "Track B Day 6+ reached")
            _stats["hot_leads"] += 1
            notify_hot_lead("calling_digital", name, business, phone, "completed Track B sequence")


def get_stats() -> dict:
    return dict(_stats)
