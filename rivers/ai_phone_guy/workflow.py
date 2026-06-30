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
from rivers.ai_phone_guy.sequences import (
    TYLER_PROSPECT_TAG,
    vertical_for_tags,
    get_sequence,
    render_message,
    BOOKING_LINK,
    SEND_SCHEDULE,
)
from rivers.ai_phone_guy import enrollment_store
from rivers.ai_phone_guy.hot_leads import check_for_hot_leads

GHL_BASE = "https://services.leadconnectorhq.com"


def _ghl_key() -> str:
    return os.environ.get("GHL_API_KEY", "")


def _ghl_location() -> str:
    return os.environ.get("GHL_LOCATION_ID", "")


def _ghl_headers() -> dict:
    return {"Authorization": f"Bearer {_ghl_key()}", "Version": "2021-07-28", "Content-Type": "application/json"}

# Per-process working cache of enrollments. Durable source of truth is the
# randy_enrollments table via enrollment_store; this dict is just a hot cache
# populated from the store at the start of each run so sequence processing
# inside a single run stays fast. It is NOT the enrollment-state authority —
# that's the DB, so enrollments survive a restart (the bug PR #121 left open).
_enrolled = {}  # contact_id -> {vertical, enrolled_at, last_step, contact}
_stats = {"enrolled": 0, "hot_leads": 0, "messages_sent": 0}


def randy_run():
    """Main loop — called by scheduler every 4 hours.

    Returns a structured per-run summary string (>=200 chars) so the post-run
    hook in app.py persists it as raw_output instead of the heartbeat fallback.
    CRO RED flag 2026-06-27T21:45Z: prior heartbeat-only logs masked the fact
    that Randy was leaking contacts at the enrollment step (29 stuck, +4/day).
    """
    from datetime import datetime as _dt
    started_at = _dt.now()
    log_info("ai_phone_guy", "=== RANDY RUN START ===")
    tally = {
        "prospects_found": 0,
        "enrolled_attempted": 0,
        "enrolled_tag_confirmed": 0,
        "enrolled_tag_failed": 0,
        "messages_sent_attempted": 0,
        "messages_sent_confirmed": 0,
        "hot_leads_flagged": 0,
        "errors": 0,
        "per_vertical": {},
    }
    try:
        # Step 0: Hydrate the per-process cache from durable storage so a freshly
        # restarted process knows who's already enrolled (otherwise everyone
        # looks "new" again — the resetting-dict bug).
        _enrolled.clear()
        _enrolled.update(enrollment_store.all_enrollments())

        # Step 1: Find new contacts with the bare tyler-prospect tag
        new_contacts = _find_new_prospects()
        tally["prospects_found"] = len(new_contacts)
        for contact in new_contacts:
            try:
                vert = contact.get("_vertical", "unknown")
                tally["per_vertical"][vert] = tally["per_vertical"].get(vert, 0) + 1
                tally["enrolled_attempted"] += 1
                ok = _enroll_contact(contact)
                if ok:
                    tally["enrolled_tag_confirmed"] += 1
                else:
                    tally["enrolled_tag_failed"] += 1
            except Exception as e:
                tally["errors"] += 1
                log_error("ai_phone_guy", f"Per-contact enrollment failed: {e}")

        # Step 2: Process sequence steps for enrolled contacts
        _process_sequences()

        # Step 3: Check for hot leads
        enrolled_contacts = _fetch_enrolled_contacts()
        check_for_hot_leads(enrolled_contacts)

        tally["messages_sent_confirmed"] = _stats["messages_sent"]
        duration_sec = (_dt.now() - started_at).total_seconds()
        log_info("ai_phone_guy", f"=== RANDY RUN COMPLETE === Enrolled: {_stats['enrolled']} | Messages: {_stats['messages_sent']}")
        return _format_run_summary(tally, duration_sec, started_at)
    except Exception as e:
        log_error("ai_phone_guy", f"Randy run failed: {e}")
        return (
            f"RANDY RUN FAILED — {started_at.isoformat()}\n"
            f"  Error: {e}\n"
            f"  Tallies before failure: {tally}\n"
            f"  Action: CRO + B&T review the workflow.py traceback in agent_logs."
        )


def _format_run_summary(tally: dict, duration_sec: float, started_at) -> str:
    """>=200-char per-run telemetry so CRO sweeps + morning brief see real work.

    Critical surfacing: enrolled_tag_failed > 0 is the leak signal — those
    contacts hit _enroll_contact() but the GHL add-tag POST to write
    'sequence-active' failed, so they'll show up as 'new' on the next run
    (in-memory _enrolled is per-process). 29 stuck contacts as of 2026-06-27
    traced to this exact silent-failure path.
    """
    leak_warning = (
        ""
        if tally["enrolled_tag_failed"] == 0
        else f"\n  LEAK SIGNAL: {tally['enrolled_tag_failed']} contacts enrolled in-memory but GHL add-tag POST failed — these will re-appear as 'new' next run."
    )
    per_vert = tally["per_vertical"]
    vert_line = "  ".join(f"{k}: {v}" for k, v in sorted(per_vert.items())) or "(none)"
    return (
        f"RANDY RUN — {started_at.isoformat()} ({duration_sec:.1f}s)\n"
        f"  Prospects found (tyler-prospect-* tagged, no sequence-active): {tally['prospects_found']}\n"
        f"  Enrollment attempts: {tally['enrolled_attempted']}\n"
        f"    Confirmed (GHL tag landed): {tally['enrolled_tag_confirmed']}\n"
        f"    Failed (in-memory only — will leak): {tally['enrolled_tag_failed']}"
        f"{leak_warning}\n"
        f"  Sequence messages sent: {tally['messages_sent_confirmed']}\n"
        f"  Hot leads flagged: {tally['hot_leads_flagged']}\n"
        f"  Per-vertical: {vert_line}\n"
        f"  Errors during run: {tally['errors']}\n"
        f"  Source: GHL AIPG location (still on GHL — Twenty migration pending).\n"
        f"  Iron rules: ICP-specific copy, no pricing, written to OWNER."
    )


def _find_new_prospects() -> list:
    """Find contacts carrying Tyler's bare `tyler-prospect` tag that aren't yet enrolled.

    Tyler stamps `tyler-prospect` + `cold-email` + `aiphoneguy` + a SEPARATE
    industry tag (e.g. `plumbing`, `roofing`, `dental`, `personal-injury-law`).
    There is no compound `tyler-prospect-plumber` tag in live data — searching
    those matched zero contacts (the 0-enrollment bug). We search the bare tag
    and derive the vertical from the contact's industry tag.

    Skips contacts that already carry `sequence-active` (enrolled in GHL) or are
    already recorded in the durable enrollment store (survives restarts), and
    skips contacts with no recognized industry tag (no AIPG sequence to fire).
    """
    if not _ghl_key():
        log_info("ai_phone_guy", "[DRY RUN] No GHL_API_KEY — skipping prospect scan")
        return []

    new_contacts = []
    no_vertical = 0
    seen_ids = set()

    # Page through all tyler-prospect contacts (GHL caps page size at 100).
    url = f"{GHL_BASE}/contacts/"
    params = {"locationId": _ghl_location(), "query": TYLER_PROSPECT_TAG, "limit": 100}
    page_guard = 0
    while True:
        page_guard += 1
        if page_guard > 50:  # safety: never loop forever
            break
        try:
            resp = requests.get(url, headers=_ghl_headers(), params=params)
        except Exception as e:
            log_error("ai_phone_guy", f"GHL search error for {TYLER_PROSPECT_TAG}: {e}")
            break
        if resp.status_code != 200:
            log_error("ai_phone_guy", f"GHL search failed for {TYLER_PROSPECT_TAG}: {resp.status_code}")
            break

        body = resp.json()
        contacts = body.get("contacts", [])
        if not contacts:
            break

        for c in contacts:
            cid = c.get("id")
            if not cid or cid in seen_ids:
                continue
            seen_ids.add(cid)
            tags = [str(t).strip().lower() for t in (c.get("tags", []) or [])]
            if TYLER_PROSPECT_TAG not in tags:
                continue
            if "sequence-active" in tags:
                continue
            if cid in _enrolled or enrollment_store.is_enrolled(cid):
                continue
            vertical = vertical_for_tags(tags)
            if not vertical:
                no_vertical += 1
                continue
            c["_vertical"] = vertical
            new_contacts.append(c)

        # Advance pagination using GHL's meta cursor, if present.
        meta = body.get("meta", {}) or {}
        start_after = meta.get("startAfter")
        start_after_id = meta.get("startAfterId")
        if not start_after_id or len(contacts) < params["limit"]:
            break
        params = {
            "locationId": _ghl_location(),
            "query": TYLER_PROSPECT_TAG,
            "limit": 100,
            "startAfter": start_after,
            "startAfterId": start_after_id,
        }

    if no_vertical:
        log_info("ai_phone_guy", f"Skipped {no_vertical} tyler-prospect contacts with no recognized industry tag")
    log_info("ai_phone_guy", f"Found {len(new_contacts)} new prospects")
    return new_contacts


def _enroll_contact(contact: dict) -> bool:
    """Immediately enroll a contact into their ICP sequence.

    Returns True if the GHL 'sequence-active' tag was durably written (contact
    enrolled durably, or a dry-run with no GHL key). Returns False if the
    in-memory enrollment succeeded but the GHL tag write failed — in that case
    the contact will re-appear as 'new' on the next run (the LEAK that surfaced
    as 29 stuck contacts per CRO RED flag 2026-06-27T21:45Z).

    ROOT CAUSE of the 29-contact "ZERO enrollment tags" leak: the prior code
    did requests.put(f"{GHL_BASE}/contacts/{cid}", json={"tags": ...}) with NO
    response check. Two compounding defects:
      1. Wrong endpoint. The LeadConnector v2 API does NOT mutate tags through
         the contact-Update endpoint (PUT /contacts/{id}); a tags-only body is
         silently ignored. Tags must be added via the dedicated Add-Tags
         endpoint POST /contacts/{id}/tags — the same pattern Joshua's
         pit_wall.py already uses successfully (pit_wall.py ~lines 53/57).
      2. No status check. Even when the call failed, enrollment was treated as
         done, so the contact was marked enrolled in-memory and never retried.
    Both are fixed here: dedicated Add-Tags endpoint + status check + truthful
    durable return. No in-place mutation of the source contact's tags.
    """
    cid = contact.get("id")
    vertical = contact.get("_vertical")
    name = f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip()

    _enrolled[cid] = {
        "vertical": vertical,
        "enrolled_at": datetime.now(),
        "last_step": -1,
        "contact": contact,
    }
    # Persist durably so this contact is NOT re-processed as "new" after a
    # restart/redeploy (the resetting-dict bug). Idempotent upsert.
    enrollment_store.record_enrollment(cid, vertical, contact, last_step=-1)

    # Add the sequence-active tag in GHL via the dedicated Add-Tags endpoint.
    # If this fails, we have an in-memory leak.
    tag_durable = False
    if _ghl_key():
        url = f"{GHL_BASE}/contacts/{cid}/tags"
        try:
            resp = requests.post(
                url, headers=_ghl_headers(), json={"tags": ["sequence-active"]}, timeout=15
            )
            if resp.status_code in (200, 201, 204):
                tag_durable = True
            else:
                log_error(
                    "ai_phone_guy",
                    f"LEAK: GHL add-tag POST failed for {cid} ({name}) status={resp.status_code} body={resp.text[:200]} — contact enrolled in-memory only, will re-appear next run"
                )
        except Exception as e:
            log_error(
                "ai_phone_guy",
                f"LEAK: GHL add-tag POST raised for {cid} ({name}): {e} — contact enrolled in-memory only, will re-appear next run"
            )
    else:
        # No GHL key = dry-run path; treat as durable so dry-runs don't mark leak
        tag_durable = True

    _stats["enrolled"] += 1
    log_enrollment("ai_phone_guy", cid, name, f"sequence-{vertical}" + ("" if tag_durable else " [TAG-FAILED]"))
    log_info("ai_phone_guy", f"ENROLLED: {name} → {vertical} sequence" + ("" if tag_durable else " (in-memory only)"))

    # Queue Day 0 message — will send when the vertical's send window opens
    schedule = SEND_SCHEDULE.get(vertical, {})
    if is_within_send_window(schedule):
        _send_sequence_step(cid, 0)
    else:
        log_info("ai_phone_guy", f"QUEUED: {name} Day 0 — waiting for {vertical} send window ({schedule.get('day', '?')} {schedule.get('hour', '?')}:{schedule.get('minute', 0):02d} CST)")

    return tag_durable


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
    enrollment_store.update_last_step(contact_id, step_day)
    _stats["messages_sent"] += 1
    log_sequence_event("ai_phone_guy", contact_id, f"{channel}_sent", f"day_{step_day}")
    log_info("ai_phone_guy", f"SENT Day {step_day} {channel} to {name} ({vertical})")


def _send_ghl_sms(contact_id: str, body: str):
    if not _ghl_key():
        log_info("ai_phone_guy", f"[DRY RUN] SMS to {contact_id}: {body[:80]}...")
        return
    url = f"{GHL_BASE}/conversations/messages"
    payload = {
        "type": "SMS",
        "contactId": contact_id,
        "message": body,
    }
    try:
        resp = requests.post(url, headers=_ghl_headers(), json=payload)
        if resp.status_code not in (200, 201):
            log_error("ai_phone_guy", f"SMS send failed: {resp.status_code} {resp.text}")
    except Exception as e:
        log_error("ai_phone_guy", f"SMS send error: {e}")


def _send_ghl_email(contact_id: str, subject: str, body: str):
    if not _ghl_key():
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
        resp = requests.post(url, headers=_ghl_headers(), json=payload)
        if resp.status_code not in (200, 201):
            log_error("ai_phone_guy", f"Email send failed: {resp.status_code} {resp.text}")
    except Exception as e:
        log_error("ai_phone_guy", f"Email send error: {e}")


def _fetch_enrolled_contacts() -> list:
    """Return enrolled contacts enriched with live GHL signals for hot-lead detection."""
    contacts = []
    for cid, data in list(_enrolled.items()):
        contact = dict(data.get("contact", {}))
        contact["id"] = cid
        contact["tags"] = ["sequence-active"]
        contact["has_sms_reply"] = False
        contact["email_opens"] = 0

        if _ghl_key():
            try:
                # Fetch fresh contact from GHL to get current tags
                resp = requests.get(f"{GHL_BASE}/contacts/{cid}", headers=_ghl_headers())
                if resp.status_code == 200:
                    ghl_contact = resp.json().get("contact", {})
                    contact["tags"] = ghl_contact.get("tags", contact["tags"])

                # Check for inbound SMS reply via conversations API
                conv_resp = requests.get(
                    f"{GHL_BASE}/conversations/search",
                    headers=_ghl_headers(),
                    params={"contactId": cid, "locationId": _ghl_location()},
                )
                if conv_resp.status_code == 200:
                    convs = conv_resp.json().get("conversations", [])
                    for conv in convs:
                        # SMS inbound reply
                        if conv.get("type") == "TYPE_SMS" and conv.get("lastMessageType") == "TYPE_SMS":
                            if conv.get("unreadCount", 0) > 0 or conv.get("direction") == "inbound":
                                contact["has_sms_reply"] = True
                        # Email open tracking
                        contact["email_opens"] += conv.get("emailOpenCount", 0)
            except Exception as e:
                log_error("ai_phone_guy", f"Failed to enrich contact {cid}: {e}")

        contacts.append(contact)
    return contacts


def get_stats() -> dict:
    return dict(_stats)
