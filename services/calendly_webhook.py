"""services/calendly_webhook.py -- Calendly booking webhook normalizer.

Per Boardroom Readout 004 P1 (2026-07-09):
  "Also wire Calendly booking webhook -> CRM event + SMS."

Calendly fires `invitee.created` when someone books via a shared link, embed,
or a downstream form (Meta lead-form → Calendly redirect, etc.). This module
verifies Calendly's HMAC-SHA256 signature, normalizes the payload into our
existing IntentInboundEvent contract, and hands off to handle_event() so:

  1. Audit row lands in intent_inbound_events
  2. Twenty gets a Person upsert + Signal record (per-brand routing)
  3. core.notifier.notify_hot_reply fires SMS + email to Michael (since
     response_type=meeting_booked is already in _HOT_REPLY_TYPES)

Brand routing uses the scheduled_event.name to distinguish which brand's
calendar was booked. Falls back to a `brand` query-param override or the
`CALENDLY_DEFAULT_BRAND` env for anything unrecognized.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Brand routing -- match scheduled_event.name against per-brand patterns
# ---------------------------------------------------------------------------

_EVENT_NAME_TO_BRAND: Dict[str, str] = {
    # explicit brand-tagged events
    "avi diagnostic":        "avi",
    "avi diagnostic call":   "avi",
    "diagnostic call":       "avi",  # AvI default
    "wd strategy":           "wd",
    "worship digital":       "wd",
    "strategy session":      "wd",   # calling-michael/strategy-session (mem)
    "book'd demo":           "bookd",
    "bookd demo":            "bookd",
    "aipg":                  "aipg",
    "phone guy":             "aipg",
    "the ai phone guy":      "aipg",
    "p&p":                   "pp",
    "paper and purpose":     "pp",
}


def _brand_from_event_name(event_name: str) -> Optional[str]:
    if not event_name:
        return None
    n = event_name.strip().lower()
    for pattern, brand in _EVENT_NAME_TO_BRAND.items():
        if pattern in n:
            return brand
    return None


# ---------------------------------------------------------------------------
# Payload normalization
# ---------------------------------------------------------------------------


def calendly_payload_to_event(
    raw_payload: Dict[str, Any],
    brand_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Return an IntentInboundEvent-shaped dict (ready for handle_event()) OR
    a dict {ok: false, ...} when the payload can't be normalized.

    Args:
      raw_payload: JSON body Calendly POSTed.
      brand_override: optional brand slug from a `?brand=` query param — takes
        precedence over event-name inference so a routing form or Meta redirect
        that hits the same underlying Calendly event can still land in the
        right brand's workspace.
    """
    if not isinstance(raw_payload, dict):
        return {"ok": False, "reason": f"expected dict, got {type(raw_payload).__name__}"}

    event_kind = (raw_payload.get("event") or "").strip()
    # invitee.created = new booking; invitee_no_show.created = attended-no-show
    # (still useful signal). Skip cancellations (they're a distinct signal
    # you'd want to model separately, not as a hot reply).
    if event_kind not in ("invitee.created", "invitee_no_show.created"):
        return {
            "ok": False,
            "reason": f"unhandled event kind: {event_kind!r} (want invitee.created or invitee_no_show.created)",
        }

    payload = raw_payload.get("payload") or {}
    scheduled = payload.get("scheduled_event") or {}
    event_name = (scheduled.get("name") or "").strip()

    brand = (brand_override or "").strip().lower() or _brand_from_event_name(event_name)
    if not brand:
        brand = (os.getenv("CALENDLY_DEFAULT_BRAND") or "wd").strip().lower()

    email = (payload.get("email") or "").strip().lower()
    first_name = (payload.get("first_name") or "").strip()
    last_name = (payload.get("last_name") or "").strip()
    full_name = (payload.get("name") or f"{first_name} {last_name}".strip()).strip()

    person_ref: Dict[str, str] = {}
    if email:
        person_ref["email"] = email
    if payload.get("uri"):
        # Calendly's invitee URI is stable and unique — great external_id
        person_ref["external_id"] = payload["uri"]

    if not person_ref:
        return {
            "ok": False,
            "reason": "no invitee email or uri in payload",
        }

    # Timestamp: prefer the event start_time (that's the actual meeting slot),
    # fall back to created_at (booking creation) if scheduled_event is thin.
    ts_raw = (
        scheduled.get("start_time")
        or raw_payload.get("created_at")
        or payload.get("created_at")
    )
    try:
        ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")) if ts_raw else datetime.now(timezone.utc)
    except (ValueError, TypeError):
        ts = datetime.now(timezone.utc)

    subtype = "no_show" if event_kind == "invitee_no_show.created" else event_name[:60] or "booked"

    event: Dict[str, Any] = {
        "brand": brand,
        "person_ref": person_ref,
        "channel": "web_visitor",   # Calendly is web-mediated; existing enum value
        "response_type": "meeting_booked",  # triggers hot-reply SMS + email
        "subtype": subtype,
        "raw_body": {
            **raw_payload,
            "_calendly_source": event_name,
            "_calendly_invitee_name": full_name,
        },
        "timestamp": ts.isoformat(),
    }
    return {"ok": True, "event": event, "brand": brand, "event_name": event_name}


# ---------------------------------------------------------------------------
# Signature verification (Calendly Ed25519 -- HMAC-SHA256 in v2 signing)
#
# Calendly's webhook signature format:
#   Calendly-Webhook-Signature: t=<unix_ts>,v1=<hex_hmac_sha256>
#
# We verify by:
#   1. Parse t + v1 from header
#   2. Concatenate: signed_payload = "{t}.{raw_body_utf8}"
#   3. HMAC-SHA256(signing_key, signed_payload) -- must match v1
#   4. Optional: reject if timestamp is >5 min old (replay guard)
# ---------------------------------------------------------------------------


_SIG_TOLERANCE_SECONDS = 300


def _parse_sig_header(header: str) -> Tuple[Optional[str], Optional[str]]:
    if not header:
        return None, None
    parts = {}
    for kv in header.split(","):
        if "=" in kv:
            k, v = kv.split("=", 1)
            parts[k.strip()] = v.strip()
    return parts.get("t"), parts.get("v1")


def verify_signature(raw_body: bytes, signature_header: str, signing_key: Optional[str] = None) -> Tuple[bool, str]:
    """Verify Calendly's HMAC-SHA256 signature.

    Returns (ok, reason). reason is a short diagnostic when ok=False;
    empty when ok=True. Fails closed when signing_key is absent so the
    endpoint can't be spoofed by omitting env config.
    """
    key = (signing_key or os.getenv("CALENDLY_WEBHOOK_SIGNING_KEY") or "").strip()
    if not key:
        return False, "CALENDLY_WEBHOOK_SIGNING_KEY not set"
    if not signature_header:
        return False, "Calendly-Webhook-Signature header missing"
    ts_str, provided_hex = _parse_sig_header(signature_header)
    if not ts_str or not provided_hex:
        return False, f"malformed signature header (t={ts_str!r} v1={provided_hex!r})"
    # Replay guard.
    try:
        ts_i = int(ts_str)
        drift = abs(int(datetime.now(timezone.utc).timestamp()) - ts_i)
        if drift > _SIG_TOLERANCE_SECONDS:
            return False, f"signature timestamp drift {drift}s > {_SIG_TOLERANCE_SECONDS}s"
    except ValueError:
        return False, f"non-integer timestamp: {ts_str!r}"

    signed = f"{ts_str}.".encode() + raw_body
    expected = hmac.new(key.encode(), signed, hashlib.sha256).hexdigest()
    if hmac.compare_digest(expected, provided_hex):
        return True, ""
    return False, "signature mismatch"


__all__ = [
    "calendly_payload_to_event",
    "verify_signature",
    "_brand_from_event_name",
]
