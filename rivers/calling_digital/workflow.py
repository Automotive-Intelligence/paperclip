"""Brenda — RevOps Agent for Calling Digital (Attio).

Monitors Attio for new contacts from OwnerPhones CSV imports.
Scores contacts, assigns Track A or B, fires email sequences.
Flags hot leads to Marcus.
Schedule: Every 2 hours.
"""

import os
import json
import requests
from datetime import datetime, timedelta
from core.logger import log_enrollment, log_sequence_event, log_info, log_error, log_hot_lead
from core.notifier import notify_hot_lead
from core.hours import is_within_send_window, is_email_hours
from rivers.calling_digital.scoring import score_contact, assign_track
from rivers.calling_digital.sequences import get_track_sequence, render_message

ATTIO_BASE = "https://api.attio.com/v2"


def _attio_key() -> str:
    return os.environ.get("ATTIO_API_KEY", "")


def _attio_headers() -> dict:
    return {"Authorization": f"Bearer {_attio_key()}", "Content-Type": "application/json"}


BOOKING_LINK = os.environ.get("BOOKING_LINK_CD", "")

VERTICAL_MAP = {
    "med-spa": {"industry": "med spa", "send_day": "wednesday", "send_hour": 19},
    "pi-law": {"industry": "PI law firm", "send_day": "wednesday", "send_hour": 19},
    "real-estate": {"industry": "real estate", "send_day": "tuesday", "send_hour": 8},
    "home-builder": {"industry": "custom home builder", "send_day": "monday", "send_hour": 6},
}

_enrolled = {}
_stats = {"enrolled": 0, "hot_leads": 0, "messages_sent": 0}
STATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "logs",
    "calling_digital_workflow_state.json",
)


def _load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {"initialized_at": None, "seen_ids": []}
    try:
        with open(STATE_PATH, "r") as f:
            data = json.load(f)
        return {
            "initialized_at": data.get("initialized_at"),
            "seen_ids": list(data.get("seen_ids", [])),
        }
    except Exception as e:
        log_error("calling_digital", f"Failed to load workflow state: {e}")
        return {"initialized_at": None, "seen_ids": []}


def _save_state(state: dict):
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w") as f:
            json.dump(state, f, indent=2, sort_keys=True)
    except Exception as e:
        log_error("calling_digital", f"Failed to save workflow state: {e}")


_state = _load_state()


def _mark_seen(contact_id: str):
    seen_ids = set(_state.get("seen_ids", []))
    if contact_id in seen_ids:
        return
    seen_ids.add(contact_id)
    _state["seen_ids"] = sorted(seen_ids)
    _save_state(_state)


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
    if not _attio_key():
        log_info("calling_digital", "[DRY RUN] No ATTIO_API_KEY — skipping")
        return []

    contacts = []
    current_ids = set()
    seen_ids = set(_state.get("seen_ids", []))
    for vertical_tag, meta in VERTICAL_MAP.items():
        try:
            url = f"{ATTIO_BASE}/objects/people/records/query"
            # Query the AVO custom 'vertical' single-select attribute
            # (created via OwnerPhones import — replaces the old 'tags' query
            # which returned 400 because People object has no 'tags' attribute)
            payload = {
                "filter": {"vertical": {"$eq": vertical_tag}},
                "limit": 100,
            }
            resp = requests.post(url, headers=_attio_headers(), json=payload)
            if resp.status_code == 200:
                records = resp.json().get("data", [])
                for r in records:
                    rid = r.get("id", {}).get("record_id", "")
                    if rid:
                        current_ids.add(rid)
                    if rid and rid not in _enrolled and rid not in seen_ids:
                        attrs = r.get("values", {})
                        contact = _parse_attio_record(attrs, vertical_tag, meta)
                        contact["id"] = rid
                        contact["_raw"] = r
                        contacts.append(contact)
            else:
                log_error("calling_digital", f"Attio query failed for {vertical_tag}: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            log_error("calling_digital", f"Attio query error for {vertical_tag}: {e}")

    if not _state.get("initialized_at"):
        _state["initialized_at"] = datetime.now().isoformat()
        _state["seen_ids"] = sorted(current_ids)
        _save_state(_state)
        log_info("calling_digital", f"Workflow baseline seeded with {len(current_ids)} existing Attio contacts; only net-new contacts will enroll going forward")
        return []

    log_info("calling_digital", f"Found {len(contacts)} new contacts")
    return contacts


def _parse_attio_record(attrs: dict, vertical: str, meta: dict) -> dict:
    """Parse Attio People record values into a normalized contact dict.

    Attio's People object nests name parts, emails, phones, and locations
    in specific shapes. This handles them all and falls back gracefully
    when fields are missing.
    """
    def _first(key):
        vals = attrs.get(key, [])
        if vals and isinstance(vals, list):
            return vals[0] if isinstance(vals[0], dict) else {"value": vals[0]}
        return {}

    name_obj = _first("name")
    first_name = name_obj.get("first_name", "") or ""
    last_name = name_obj.get("last_name", "") or ""

    email_obj = _first("email_addresses")
    email = email_obj.get("email_address", "") or email_obj.get("value", "") or ""

    phone_obj = _first("phone_numbers")
    phone = phone_obj.get("original_phone_number", "") or phone_obj.get("phone_number", "") or ""

    company_obj = _first("company")
    # Company is a record-reference; in queries it returns target_record_id
    business_name = company_obj.get("target_record_id", "") or company_obj.get("value", "") or ""

    location_obj = _first("primary_location")
    city = location_obj.get("locality", "") or ""
    state = location_obj.get("region", "") or ""

    description_obj = _first("description")
    description = description_obj.get("value", "") or ""

    return {
        "firstName": first_name,
        "lastName": last_name,
        "email": email,
        "phone": phone,
        "businessName": business_name or description[:60],  # fallback to desc snippet
        "city": city,
        "state": state,
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
    _mark_seen(cid)
    log_enrollment("calling_digital", cid, name, f"track-{track} (score={score})")
    log_info("calling_digital", f"ENROLLED: {name} → Track {track} (score={score})")

    # Send Day 1 if within vertical's send window
    vertical = contact.get("vertical", "")
    meta = VERTICAL_MAP.get(vertical, {})
    schedule = {"day": meta.get("send_day", ""), "hour": meta.get("send_hour", 9), "minute": 0}
    if is_within_send_window(schedule):
        _send_sequence_step(cid, 1)
    else:
        log_info("calling_digital", f"QUEUED: {name} Day 1 — waiting for {vertical} send window ({meta.get('send_day', '?')} {meta.get('send_hour', '?')}:00 CST)")


def _process_sequences():
    """Send due sequence steps — only during email business hours (Mon-Fri 7am-9pm CST)."""
    if not is_email_hours():
        log_info("calling_digital", "Outside email hours — skipping sequence sends")
        return

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
    if not _attio_key() or not to_email:
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
        requests.post(url, headers=_attio_headers(), json=payload)
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
