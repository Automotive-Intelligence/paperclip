"""
services/ghl_voice_ai_bridge.py — GHL Voice AI receptionist → Twenty bridge

Per file 77 (AI Receptionists — AvI, WD, Book'd). Each receptionist runs in the
AIPG GHL location as a Voice AI agent; when a call qualifies + books, the GHL
workflow POSTs the captured fields to this endpoint and we create/update the
right Twenty workspace's Person + Company so the brand seat (AvI/WD) sees the
lead in its own CRM.

Why this exists separately from the existing crm_router push:
  - crm_router routes prospect lists per agent_name + business_key (Marcus,
    Ryan_Data, etc. — the prospecting agents). The Voice AI receptionists are
    a different shape: per-call inbound capture, not bulk prospect push, with
    GHL as the originating system.
  - Book'd routes to Ryan's setup + bookd.twenty.com; surfaced as a brand here
    but the actual write needs Ryan's sign-off (per file 77). For now we log
    + tag + return ack; surface in Build & Tech for Ryan handoff.

Auth model:
  - URL-path secret (GHL workflows POST to /webhooks/ghl/voice-ai/{secret})
  - Optional HMAC-SHA256 of body via X-Webhook-Signature (GHL workflows can
    set custom headers; matches GHL_VOICE_AI_WEBHOOK_HMAC_SECRET if set).
  - Both env vars are NEW — set during the receptionist UI build:
      GHL_VOICE_AI_WEBHOOK_PATH_SECRET (required)
      GHL_VOICE_AI_WEBHOOK_HMAC_SECRET (optional)

Payload shape (the GHL workflow's Custom Webhook step posts this JSON):
  {
    "brand":            "avi" | "wd" | "bookd",
    "contact_name":     "Jane Doe",
    "phone":            "+1...",
    "email":            "jane@dealership.com",   (optional)
    "business_name":    "Smith Toyota",          (avi=dealership, wd=business, bookd=agency)
    "city":             "Plano, TX",
    "role":             "GM" | "owner" | "agent",
    "primary_pain":     "lead volume" | ...,
    "is_qualified":     true,                    (false = AI declined to book)
    "calendar_event_id":"...",                   (optional, when booking landed)
    "calendar_label":   "AvI Diagnostic" | ...
  }

Returns JSON summarizing what was routed + the Twenty record id.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)


# Brand → Twenty business_key the existing tools/twenty.py:_workspace_config
# already knows. Book'd is intentionally NOT auto-routed here — it touches
# Ryan's GHL setup + mirrors to bookd.twenty.com per his sign-off. We log +
# return without writing for Book'd until Ryan confirms.
_BRAND_TO_TWENTY_KEY = {
    "avi":   "autointelligence",
    "wd":    "callingdigital",       # WD workspace (Twenty AvI bootstrap)
    "bookd": None,                   # Hold for Ryan confirmation
}


def verify_signature(secret: str, body: bytes, sig_header: str) -> bool:
    """HMAC-SHA256 verify against `X-Webhook-Signature: sha256=<hex>`.

    Returns True when no HMAC secret is configured (optional defense layer);
    otherwise must match the body's HMAC exactly.
    """
    if not secret:
        return True
    if not sig_header or not sig_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header)


def _split_name(full: str) -> tuple[str, str]:
    full = (full or "").strip()
    if not full:
        return "", ""
    parts = full.split(None, 1)
    return parts[0], parts[1] if len(parts) > 1 else ""


def _route_to_twenty(brand: str, prospect: Dict[str, Any]) -> Dict[str, Any]:
    """Bridge call → Twenty Person + Company in the right workspace via the
    existing push_prospects_to_twenty contract. Returns a single-element
    summary suitable for the webhook response."""
    business_key = _BRAND_TO_TWENTY_KEY.get(brand)
    if not business_key:
        return {
            "status": "skipped",
            "reason": f"brand {brand!r} not routed to Twenty (held for Ryan sign-off on Book'd)",
        }

    # tools/twenty.push_prospects_to_twenty expects a list of prospect dicts.
    # The mapping mirrors the prospecting agents' shape so we share the same
    # E.164 normalization + dedup + judge gates already battle-tested in prod.
    first, last = _split_name(prospect.get("contact_name") or "")
    p = {
        "business_name":  prospect.get("business_name") or "",
        "contact":        prospect.get("contact_name") or "",
        "first_name":     first,
        "last_name":      last,
        "phone":          prospect.get("phone") or "",
        "email":          prospect.get("email") or "",
        "website":        prospect.get("website") or "",
        "city":           prospect.get("city") or "",
        "job_title":      prospect.get("role") or "",
        "verified_fact":  prospect.get("primary_pain") or "",
        "trigger_event":  prospect.get("calendar_label") or "voice-ai inbound",
    }
    try:
        from tools.twenty import push_prospects_to_twenty
        results = push_prospects_to_twenty(
            [p],
            source_agent="ghl_voice_ai_bridge",
            business_key=business_key,
        )
    except Exception as e:
        logger.exception("[ghl-voice-bridge] Twenty push raised: %s", e)
        return {
            "status": "twenty_error",
            "reason": f"{type(e).__name__}: {e}",
        }
    if not results:
        return {"status": "twenty_empty", "reason": "writer returned no results"}
    r0 = results[0]
    return {
        "status":         r0.get("status") or "unknown",
        "twenty_id":      r0.get("contact_id") or "",
        "provider":       "twenty",
        "business_key":   business_key,
        "twenty_status":  r0.get("status") or "",
    }


def handle_webhook(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Top-level entry: parse + route + audit-log. Idempotent — Twenty's own
    dedup (by email; PR #67 dedup by domain too) protects against re-fires."""
    brand = (payload.get("brand") or "").strip().lower()
    if brand not in _BRAND_TO_TWENTY_KEY:
        return {
            "status": "bad_payload",
            "reason": f"brand must be one of {sorted(_BRAND_TO_TWENTY_KEY)} (got {brand!r})",
        }
    routed = _route_to_twenty(brand, payload)

    # Light audit log (full payload structured so the morning brief can read it).
    try:
        from services.database import execute_query
        execute_query(
            """
            CREATE TABLE IF NOT EXISTS ghl_voice_ai_routings (
                id              SERIAL PRIMARY KEY,
                brand           TEXT NOT NULL,
                contact_name    TEXT,
                business_name   TEXT,
                phone           TEXT,
                email           TEXT,
                city            TEXT,
                role            TEXT,
                primary_pain    TEXT,
                is_qualified    BOOLEAN,
                calendar_label  TEXT,
                twenty_status   TEXT,
                twenty_id       TEXT,
                routed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                raw             JSONB
            )
            """
        )
        execute_query(
            """
            INSERT INTO ghl_voice_ai_routings
                (brand, contact_name, business_name, phone, email, city, role,
                 primary_pain, is_qualified, calendar_label, twenty_status,
                 twenty_id, raw)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                brand,
                payload.get("contact_name"),
                payload.get("business_name"),
                payload.get("phone"),
                payload.get("email"),
                payload.get("city"),
                payload.get("role"),
                payload.get("primary_pain"),
                bool(payload.get("is_qualified")),
                payload.get("calendar_label"),
                routed.get("twenty_status") or routed.get("status"),
                routed.get("twenty_id"),
                json.dumps(payload, default=str),
            ),
        )
    except Exception as e:
        logger.warning("[ghl-voice-bridge] audit-log persist failed: %s", e)

    return {
        "ok":       routed.get("status") in {"created", "duplicate_skipped", "skipped"},
        "brand":    brand,
        "routed":   routed,
        "received": {k: payload.get(k) for k in
                    ("contact_name", "business_name", "phone", "email",
                     "city", "role", "is_qualified", "calendar_label")},
    }
