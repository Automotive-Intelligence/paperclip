"""AI for Business DFW: date-bound registrant sends (the rails half).

Evergreen sequences live in GHL workflows (tags are the interface); everything
keyed to the event date lives here so no workflow needs monthly edits:
  - T-1 day reminder SMS and morning-of SMS (daily scheduler tick)
  - one-time venue address announcement (scripts/aifb_announce_address.py)

Config (env): AIFB_EVENT_DATE=YYYY-MM-DD (no sends when unset),
AIFB_EVENT_ADDRESS (optional until the venue locks). Paperclip runtime reads
Railway-direct env; Doppler paperclip/prd is the source of truth copy.
"""
import logging
import os
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from tools.ghl import _ghl_request

logger = logging.getLogger(__name__)
CST = ZoneInfo("America/Chicago")
EVENT_TAG = "aifb-evt1"
DETAILS_URL = "theaiphoneguy.com/meetup"


def _event_date() -> Optional[date]:
    raw = (os.getenv("AIFB_EVENT_DATE") or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        logger.error("[aifb] AIFB_EVENT_DATE invalid: %r", raw)
        return None


def _address_line() -> str:
    addr = (os.getenv("AIFB_EVENT_ADDRESS") or "").strip()
    return f"Address: {addr}." if addr else f"Details: {DETAILS_URL}."


def list_registrants() -> List[Dict[str, str]]:
    """All GHL contacts tagged aifb-evt1 that have a phone number."""
    loc = (os.getenv("GHL_LOCATION_ID") or "").strip()
    # Dedupe by contact id: GHL's list paging needs BOTH startAfterId and
    # startAfter from meta, and a wrong cursor silently re-serves page one.
    # Seen-id tracking makes duplicate sends structurally impossible either way.
    seen: Dict[str, Dict[str, str]] = {}
    start_after_id = None
    start_after = None
    for _page in range(10):  # safety cap; registrant counts are in the tens
        params = {"locationId": loc, "limit": 100}
        if start_after_id:
            params["startAfterId"] = start_after_id
            params["startAfter"] = start_after
        data = _ghl_request("aifb_list_contacts", "GET", "/contacts/", params=params)
        contacts = data.get("contacts", [])
        new_ids = 0
        for c in contacts:
            cid = c.get("id")
            if not cid or cid in seen:
                continue
            new_ids += 1
            if EVENT_TAG in (c.get("tags") or []) and c.get("phone"):
                seen[cid] = {
                    "id": cid,
                    "first": (c.get("firstName") or "there").strip() or "there",
                    "phone": c["phone"],
                }
            else:
                seen[cid] = {}
        meta = data.get("meta") or {}
        start_after_id = meta.get("startAfterId")
        start_after = meta.get("startAfter")
        if not contacts or new_ids == 0 or not start_after_id:
            break
    return [r for r in seen.values() if r]


def _send_sms(contact_id: str, message: str) -> None:
    _ghl_request(
        "aifb_reminder_sms", "POST", "/conversations/messages",
        json_body={"type": "SMS", "contactId": contact_id, "message": message},
    )


def _t1d_message(first: str) -> str:
    return (
        f"Hey {first}, Michael here. AI for Business DFW is tomorrow evening. "
        f"Doors at 6:00, demos at 6:20, done by 7:30. {_address_line()} "
        f"Reply here if anything comes up. See you there."
    )


def _morning_message(first: str) -> str:
    return (
        f"Tonight: AI for Business DFW. Doors at 6:00. {_address_line()} "
        f"Come find me and say hi when you walk in. Michael"
    )


def aifb_reminder_tick(force_mode: Optional[str] = None,
                       only_contact_id: Optional[str] = None) -> Dict[str, object]:
    """Daily tick: sends T-1d or morning-of SMS when today matches the event
    date. force_mode ('t1d'|'morning') and only_contact_id exist for live
    verification without waiting for the calendar."""
    event = _event_date()
    today = datetime.now(CST).date()
    mode = force_mode
    if mode is None:
        if event is None:
            return {"mode": None, "sent": 0, "reason": "no event date set"}
        if today == event - timedelta(days=1):
            mode = "t1d"
        elif today == event:
            mode = "morning"
        else:
            return {"mode": None, "sent": 0, "reason": "not a send day"}

    build = _t1d_message if mode == "t1d" else _morning_message
    registrants = list_registrants()
    if only_contact_id:
        registrants = [r for r in registrants if r["id"] == only_contact_id]
    sent = 0
    for r in registrants:
        try:
            _send_sms(r["id"], build(r["first"]))
            sent += 1
        except Exception:
            logger.exception("[aifb] reminder send failed for %s", r["id"])
    logger.info("[aifb] reminder tick mode=%s sent=%d/%d", mode, sent, len(registrants))
    return {"mode": mode, "sent": sent, "registrants": len(registrants)}
