"""Brenda — RevOps Agent for Worship Digital (Attio).

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
from rivers.calling_digital.scoring import score_contact, assign_track, territory_label
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
    """Main loop — called by scheduler every 2 hours.

    Returns a structured per-run summary string (>=200 chars) so the post-run
    hook in app.py persists it as raw_output instead of the heartbeat fallback.
    Killing the heartbeat is a hard CRO requirement — without it the morning
    briefing + CRO sweeps can't see what Brenda actually did (CRO RED flag
    2026-06-27T21:45Z).
    """
    started_at = datetime.now()
    log_info("calling_digital", "=== BRENDA RUN START ===")
    tally = {
        "scored": 0,
        "track_a": 0,
        "track_b": 0,
        "territory": {"380_corridor": 0, "greater_dfw": 0, "tx_outside": 0, "national": 0},
        "vertical": {"med-spa": 0, "pi-law": 0, "real-estate": 0, "home-builder": 0, "other": 0},
        "enrolled": 0,
        "messages_sent": 0,
        "hot_leads_flagged": 0,
        "errors": 0,
        "skipped_unscored": 0,
    }
    try:
        new_contacts = _find_new_contacts()
        for contact in new_contacts:
            try:
                tier = territory_label(contact)
                vert = (contact.get("vertical") or "other").lower()
                tally["territory"][tier] = tally["territory"].get(tier, 0) + 1
                tally["vertical"][vert if vert in tally["vertical"] else "other"] += 1
                tally["scored"] += 1
                score = score_contact(contact)
                if assign_track(score) == "B":
                    tally["track_b"] += 1
                else:
                    tally["track_a"] += 1
                _score_and_enroll(contact)
            except Exception as e:
                tally["errors"] += 1
                log_error("calling_digital", f"Per-contact processing failed: {e}")

        _process_sequences()
        _check_hot_leads()

        # Pit wall — monitor Marcus's Instantly campaign telemetry (if configured)
        _run_pit_wall()

        tally["enrolled"] = _stats["enrolled"]
        tally["messages_sent"] = _stats["messages_sent"]
        tally["hot_leads_flagged"] = _stats["hot_leads"]
        duration_sec = (datetime.now() - started_at).total_seconds()
        log_info("calling_digital", f"=== BRENDA RUN COMPLETE === Enrolled: {_stats['enrolled']} | Messages: {_stats['messages_sent']}")

        summary = _format_run_summary(tally, duration_sec, started_at)
        return summary
    except Exception as e:
        log_error("calling_digital", f"Brenda run failed: {e}")
        return (
            f"BRENDA RUN FAILED — {started_at.isoformat()}\n"
            f"  Error: {e}\n"
            f"  Tallies before failure: {tally}\n"
            f"  Action: CRO + B&T review the workflow.py traceback in agent_logs."
        )


def _format_run_summary(tally: dict, duration_sec: float, started_at) -> str:
    """Compose the >=200-char per-run summary that CRO + morning briefing read.

    Format is stable so downstream parsers don't break each time we tune a
    field. Section order: header, scored breakdown, territory ladder,
    vertical mix, enrollment + send tally, escalations, run duration.
    """
    terr = tally["territory"]
    vert = tally["vertical"]
    return (
        f"BRENDA RUN — {started_at.isoformat()} ({duration_sec:.1f}s)\n"
        f"  Scored: {tally['scored']} contacts\n"
        f"    Track A (cold, <7): {tally['track_a']}\n"
        f"    Track B (warm, >=7): {tally['track_b']}\n"
        f"  Territory ladder:\n"
        f"    +3 380 Corridor: {terr.get('380_corridor', 0)}\n"
        f"    +2 Greater DFW : {terr.get('greater_dfw', 0)}\n"
        f"    +1 TX outside  : {terr.get('tx_outside', 0)}\n"
        f"    +0 National    : {terr.get('national', 0)}\n"
        f"  Vertical mix:\n"
        f"    med-spa: {vert.get('med-spa', 0)}  pi-law: {vert.get('pi-law', 0)}  "
        f"real-estate: {vert.get('real-estate', 0)}  home-builder: {vert.get('home-builder', 0)}  "
        f"other: {vert.get('other', 0)}\n"
        f"  Loops enrollments fired: {tally['enrolled']}\n"
        f"  Sequence messages sent : {tally['messages_sent']}\n"
        f"  Hot leads flagged to Marcus: {tally['hot_leads_flagged']}\n"
        f"  Errors during run: {tally['errors']}\n"
        f"  Source: Twenty WD workspace (Attio retired 2026-06-12).\n"
        f"  Iron rules: pricing never mentioned, copy written to OWNER, ICP-specific."
    )


def _run_pit_wall():
    """Brenda's pit wall — monitors Marcus's Instantly campaign (when configured)."""
    try:
        from rivers.shared.pit_wall import pull_instantly_leads, build_race_report
        api_key = (os.environ.get("INSTANTLY_API_KEY") or "").strip()
        campaign_id = (os.environ.get("INSTANTLY_CAMPAIGN_MARCUS") or "").strip()
        if not api_key or not campaign_id:
            return
        leads = pull_instantly_leads(api_key, campaign_id)
        if not leads:
            return
        result = build_race_report("Brenda", "Worship Digital", leads)
        log_info("calling_digital", f"[Brenda PitWall]\n{result['report']}")
    except Exception as e:
        log_error("calling_digital", f"Brenda pit wall failed: {e}")


def _find_new_contacts() -> list:
    """Find new contacts in Twenty WD that haven't been enrolled.

    R1b 2026-06-28: replaces the Attio reader (Attio retired 2026-06-12 per
    PR #82). Reads tools/twenty.py for the WD workspace, paginates the People
    list, de-dupes against the seen-ids state file. Returns contact dicts in
    the same shape the rest of the workflow expects.

    Twenty's People object has no 'vertical' field today — that semantic
    lives downstream in scoring.py. Records here are loaded with
    vertical='unknown' so the territory ladder still computes correctly
    (location-based) and the vertical-mix tally surfaces 'other'. When a
    proper vertical custom-field lands in Twenty WD, _vertical_from_twenty()
    below is the one place to update.
    """
    try:
        from tools.twenty import _workspace_config, _headers, _REQUEST_TIMEOUT, twenty_ready
    except ImportError as e:
        log_error("calling_digital", f"tools.twenty not importable: {e}")
        return []

    if not twenty_ready("callingdigital"):
        log_info("calling_digital", "[DRY RUN] Twenty WD workspace not configured — skipping")
        return []

    try:
        base_url, api_key = _workspace_config("callingdigital")
    except ValueError as e:
        log_error("calling_digital", f"Twenty WD config error: {e}")
        return []

    contacts = []
    current_ids = set()
    seen_ids = set(_state.get("seen_ids", []))

    # Twenty pages via order_by + cursor. For a Phase-1 fix, pull the most
    # recently-updated 200 people, which captures all activity in a busy day
    # for a single brand workspace. Page size can lift in a follow-up.
    try:
        resp = requests.get(
            f"{base_url}/rest/people",
            headers=_headers(api_key),
            params={"limit": 200, "order_by": "createdAt[DescNullsLast]"},
            timeout=_REQUEST_TIMEOUT,
        )
    except Exception as e:
        log_error("calling_digital", f"Twenty WD query raised: {e}")
        return []

    if resp.status_code != 200:
        log_error(
            "calling_digital",
            f"Twenty WD query failed: {resp.status_code} body={resp.text[:200]}"
        )
        return []

    people = (resp.json().get("data") or {}).get("people") or []
    for person in people:
        pid = person.get("id") or ""
        if pid:
            current_ids.add(pid)
        if pid and pid not in _enrolled and pid not in seen_ids:
            contact = _parse_twenty_person(person)
            contact["id"] = pid
            contact["_raw"] = person
            contacts.append(contact)

    if not _state.get("initialized_at"):
        _state["initialized_at"] = datetime.now().isoformat()
        _state["seen_ids"] = sorted(current_ids)
        _save_state(_state)
        log_info(
            "calling_digital",
            f"Workflow baseline seeded with {len(current_ids)} existing Twenty WD contacts; "
            "only net-new contacts will enroll going forward"
        )
        return []

    log_info("calling_digital", f"Found {len(contacts)} new Twenty WD contacts")
    return contacts


def _vertical_from_twenty(person: dict) -> str:
    """Twenty People has no vertical field today. Infer from job title where
    we can; default to 'unknown' so scoring still completes. When a real
    vertical custom-field lands, this is the one switch to flip.
    """
    title = (person.get("jobTitle") or "").lower()
    if any(k in title for k in ("med spa", "medspa", "aesthetic")):
        return "med-spa"
    if any(k in title for k in ("attorney", "lawyer", "injury", "pi law", "personal injury")):
        return "pi-law"
    if any(k in title for k in ("realtor", "real estate", "broker")):
        return "real-estate"
    if any(k in title for k in ("builder", "construction", "custom home")):
        return "home-builder"
    return "unknown"


def _parse_twenty_person(person: dict) -> dict:
    """Twenty REST → contact dict matching the rest of the workflow's expectations."""
    name_obj = person.get("name") or {}
    emails_obj = person.get("emails") or {}
    phones_obj = person.get("phones") or {}

    vertical = _vertical_from_twenty(person)
    return {
        "firstName": name_obj.get("firstName", "") or "",
        "lastName": name_obj.get("lastName", "") or "",
        "email": emails_obj.get("primaryEmail", "") or "",
        "phone": phones_obj.get("primaryPhoneNumber", "") or "",
        "businessName": person.get("companyId", "") or "",
        # Twenty People doesn't carry city/state directly today — those live
        # on the linked Company. R1b ships address-blank; the territory
        # ladder will score these as 'national' until R1c joins Company data.
        "city": "",
        "state": "",
        "vertical": vertical,
        "industry": vertical,
        "revenue": 0,
        "ai_interest": False,
        "referred_by_client": False,
        "content_engaged": False,
    }


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
    """Send a specific sequence step.

    R1b 2026-06-28: Loops integration not wired yet — was Attio (retired
    2026-06-12). Until R1c lands Loops, this no-ops with an explicit log
    AND advances last_step so the contact doesn't loop on the same step.
    Critically: does NOT increment _stats["messages_sent"] — that counter
    is the truth surface for CRO + morning brief; lying with a fake +1
    while the send path is dead is worse than logging a clean no-op.
    """
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
    subject = rendered.get("subject", "")

    # No-op until Loops wire (R1c). Advance step so the contact moves through
    # the sequence on the next run even though we haven't sent anything.
    log_info(
        "calling_digital",
        f"[SEND-PATH-PENDING] Track {track} Day {step_day} to {name} ({subject!r}): "
        "Loops integration not wired yet — contact advanced in pipeline, NOT sent."
    )
    data["last_step"] = step_day
    # Intentional: do NOT increment _stats["messages_sent"]. The tally is
    # truth-surface; spurious +1s here would mask the missing-send-path gap.


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
