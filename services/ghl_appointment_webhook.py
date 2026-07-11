"""services/ghl_appointment_webhook.py -- GHL booking webhook normalizer.

Per Boardroom Readout 004 P1 (2026-07-09):
  "Also wire booking webhook -> CRM event + SMS."

GHL is the LIVE booking system for AvI/WD/Book'd/AIPG (per marketing_
deliverables/77_ai_receptionists_avi_wd_bookd.md + memory
reference_ghl_voice_ai_build_pattern). GHL fires `AppointmentCreate` and
`AppointmentUpdate` when a prospect books via:
  - GHL Voice AI receptionist (file 77 build)
  - a shared calendar link
  - a website embed / lead form redirect

This module normalizes GHL's payload into our IntentInboundEvent contract
and hands off to handle_event() so:
  1. Audit row lands in intent_inbound_events
  2. Twenty gets Person + Signal write (per-brand routing)
  3. core.notifier.notify_hot_reply fires SMS + email to Michael
     (response_type=meeting_booked is already in _HOT_REPLY_TYPES)

Brand routing uses calendarId → brand mapping. The mapping is discovered
live via `GET /calendars/` (queried today with GHL_API_KEY):
  - 5f6YW0cdV0Wrm0GVaU9W  → wd    (WD Strategy)
  - 6jpFWLfdmACdoJoaP4Wo  → avi   (AvI Diagnostic)
  - (Book'd Demo id fills in when file 77 receptionist ships)

Env override: GHL_CALENDAR_<CALID>=<brand> for anything the mapping misses.
Query-param override: `?brand=<slug>`.
Env fallback: GHL_APPOINTMENT_DEFAULT_BRAND (default "wd").

Auth: path secret (GHL_APPOINTMENT_WEBHOOK_PATH_SECRET) since GHL Private
Integration Tokens do NOT include webhook signing. Same pattern as the
DataMoon Visitor-ID endpoint. Fails closed when unset.
"""
from __future__ import annotations

import hmac
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# calendar-id → brand routing
# ---------------------------------------------------------------------------

# Known GHL calendars per brand, discovered via `GET /calendars/?locationId=...`
# 2026-07-10. Add more as file 77 receptionists ship additional calendars.
_CALENDAR_TO_BRAND: Dict[str, str] = {
    "5f6YW0cdV0Wrm0GVaU9W": "wd",     # WD Strategy
    "6jpFWLfdmACdoJoaP4Wo": "avi",    # AvI Diagnostic
    # "N1QvqGaiZcU42IZSkeG3": "wd",  # Michael Rodriguez's Demo Calendar — treat as wd
}


def _brand_from_calendar_id(calendar_id: str) -> Optional[str]:
    if not calendar_id:
        return None
    # Static map first, then env override for surface-area growth without a
    # code change: `GHL_CALENDAR_<ID>=brand` in Doppler/Railway.
    static = _CALENDAR_TO_BRAND.get(calendar_id)
    if static:
        return static
    override = (os.getenv(f"GHL_CALENDAR_{calendar_id}") or "").strip().lower()
    return override or None


# Fallback for events that carry an event/appointment title instead of a
# clean calendarId (some routing-form flows drop into a generic calendar).
_TITLE_TO_BRAND_PATTERNS: Dict[str, str] = {
    "avi diagnostic":     "avi",
    "diagnostic call":    "avi",
    "wd strategy":        "wd",
    "worship digital":    "wd",
    "strategy session":   "wd",
    "book'd demo":        "bookd",
    "bookd demo":         "bookd",
    "aipg":               "aipg",
    "phone guy":          "aipg",
}


def _brand_from_title(title: str) -> Optional[str]:
    if not title:
        return None
    t = title.strip().lower()
    for pat, brand in _TITLE_TO_BRAND_PATTERNS.items():
        if pat in t:
            return brand
    return None


# ---------------------------------------------------------------------------
# Payload normalization
# ---------------------------------------------------------------------------


def _first(*candidates: Any) -> Optional[str]:
    for c in candidates:
        if c is None:
            continue
        s = str(c).strip()
        if s:
            return s
    return None


def ghl_payload_to_event(
    raw_payload: Dict[str, Any],
    brand_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Return an IntentInboundEvent-shaped dict (ready for handle_event()) OR
    a dict {ok: false, ...} when the payload can't be normalized.

    GHL's Appointment webhook shape varies slightly by sub-account and by
    which trigger fired it; we accept the two common layouts:

    Layout A (top-level appointment):
        { "type": "AppointmentCreate", "locationId": "...",
          "appointment": { "id": "...", "calendarId": "...",
            "contactId": "...", "contactName": "...", "contactEmail": "...",
            "contactPhone": "...", "title": "...", "startTime": "...",
            "endTime": "...", "appointmentStatus": "confirmed" } }

    Layout B (flattened event):
        { "type": "AppointmentCreate", "locationId": "...", "id": "...",
          "calendarId": "...", "contactId": "...", "title": "...",
          "startTime": "...", ... }
    """
    if not isinstance(raw_payload, dict):
        return {"ok": False, "reason": f"expected dict, got {type(raw_payload).__name__}"}

    event_kind = (raw_payload.get("type") or raw_payload.get("event") or "").strip()
    # Handle both creates + updates; skip explicit deletes/cancellations.
    if event_kind not in ("AppointmentCreate", "AppointmentUpdate", "AppointmentBooked"):
        return {
            "ok": False,
            "reason": f"unhandled event kind: {event_kind!r} (want AppointmentCreate / AppointmentUpdate / AppointmentBooked)",
        }

    # Accept either shape: appointment nested OR flattened.
    appt = raw_payload.get("appointment") or raw_payload

    calendar_id = _first(
        appt.get("calendarId"),
        raw_payload.get("calendarId"),
    )
    title = _first(
        appt.get("title"),
        raw_payload.get("title"),
        appt.get("appointmentName"),
    ) or ""

    # Brand resolution priority:
    #   1. explicit brand_override (?brand= on the URL)
    #   2. calendarId → brand (static map + env override)
    #   3. title → brand (pattern match)
    #   4. GHL_APPOINTMENT_DEFAULT_BRAND env
    #   5. hardcoded "wd" default
    brand = (
        (brand_override or "").strip().lower()
        or (_brand_from_calendar_id(calendar_id) if calendar_id else None)
        or _brand_from_title(title)
        or (os.getenv("GHL_APPOINTMENT_DEFAULT_BRAND") or "wd").strip().lower()
    )

    # Person identifiers
    email = _first(
        appt.get("contactEmail"),
        appt.get("email"),
        raw_payload.get("contactEmail"),
        raw_payload.get("email"),
    )
    phone = _first(
        appt.get("contactPhone"),
        appt.get("phone"),
        raw_payload.get("contactPhone"),
        raw_payload.get("phone"),
    )
    external_id = _first(
        appt.get("id"),                    # appointment id
        appt.get("contactId"),             # contact id (both are unique in GHL)
        raw_payload.get("id"),
        raw_payload.get("contactId"),
    )

    person_ref: Dict[str, str] = {}
    if email:
        person_ref["email"] = email.lower()
    if phone:
        person_ref["phone"] = phone
    if external_id:
        person_ref["external_id"] = external_id

    if not person_ref:
        return {
            "ok": False,
            "reason": "no invitee identifier (need email OR phone OR appointment id / contactId)",
        }

    # Timestamp: prefer the meeting start_time; fall back to created_at.
    ts_raw = _first(
        appt.get("startTime"),
        appt.get("start_time"),
        appt.get("dateAdded"),
        raw_payload.get("startTime"),
        raw_payload.get("dateAdded"),
    )
    try:
        ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")) if ts_raw else datetime.now(timezone.utc)
    except (ValueError, TypeError):
        ts = datetime.now(timezone.utc)

    # subtype carries the event kind + title so the closer sees what got booked.
    subtype_bits = []
    if event_kind == "AppointmentUpdate":
        subtype_bits.append("update")
    if title:
        subtype_bits.append(title[:60])
    subtype = " | ".join(subtype_bits) or "booked"

    event: Dict[str, Any] = {
        "brand": brand,
        "person_ref": person_ref,
        "channel": "web_visitor",  # existing enum; GHL bookings are web-mediated
        "response_type": "meeting_booked",  # → notify_hot_reply fires SMS + email
        "subtype": subtype,
        "raw_body": {
            **raw_payload,
            "_ghl_source": title or "GHL Appointment",
            "_ghl_calendar_id": calendar_id or "",
        },
        "timestamp": ts.isoformat(),
    }
    return {
        "ok": True,
        "event": event,
        "brand": brand,
        "calendar_id": calendar_id,
        "event_name": title,
    }


# ---------------------------------------------------------------------------
# Path-secret auth
# ---------------------------------------------------------------------------


def path_secret_ok(sent: str) -> bool:
    """Constant-time compare against GHL_APPOINTMENT_WEBHOOK_PATH_SECRET.

    Fails closed when unset so the endpoint can't be reached until Michael
    explicitly provisions the secret in Doppler + Railway.
    """
    expected = (os.getenv("GHL_APPOINTMENT_WEBHOOK_PATH_SECRET") or "").strip()
    if not expected:
        return False
    return hmac.compare_digest(sent, expected)


__all__ = [
    "ghl_payload_to_event",
    "path_secret_ok",
    "_brand_from_calendar_id",
    "_brand_from_title",
]
