"""Smartlead webhook handler — receives lead_unsubscribed / lead_replied /
lead_bounced events, suppresses matching contact in Twenty WD.

Wired to FastAPI route in app.py:
    @app.post("/webhooks/smartlead/wd/{secret}")
    async def smartlead_wd_webhook(secret: str, request: Request):
        from services.smartlead_webhook_handler import handle_webhook
        return await handle_webhook(request, secret)

Auth model: URL-path secret (Smartlead doesn't support HMAC webhook signing).
The webhook URL includes a 32-char random secret as the last path segment.
Smartlead stores the full URL; only requests with the correct path hit us.
Equivalent security to API-key auth model. Verified 2026-06-19 with Smartlead
support that HMAC is NOT available on their webhook system.

Env vars expected:
    SMARTLEAD_WD_WEBHOOK_PATH_SECRET   — 32-char secret embedded in webhook URL
    TWENTY_WD_API_KEY                  — Twenty WD workspace API key
    TWENTY_WD_BASE_URL                 — Twenty WD base URL (default https://crm.worshipdigital.co)

Iron rules:
- Always return 200 if path secret valid — never block Smartlead's retry queue
  on Twenty downtime (log + return; Smartlead won't retry the event back)
- Never invent contact data — if email doesn't match any Twenty WD person, log
  and skip rather than create
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

_CTX = ssl.create_default_context()


# ---------- Twenty WD REST helpers ----------

def _twenty_call(method: str, path: str, body: dict | None = None) -> dict:
    """Call Twenty WD REST. Raises HTTPException on failure."""
    base = os.environ.get("TWENTY_WD_BASE_URL", "https://crm.worshipdigital.co").rstrip("/")
    key = os.environ.get("TWENTY_WD_API_KEY")
    if not key:
        raise HTTPException(status_code=500, detail="TWENTY_WD_API_KEY missing")
    url = base + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, data=data)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, context=_CTX, timeout=15) as r:
            text = r.read().decode()
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        raise HTTPException(status_code=502, detail=f"Twenty {method} {path} → {e.code}: {err[:200]}")


def find_person_by_email(email: str) -> dict | None:
    filt = urllib.parse.quote(f'emails.primaryEmail[eq]:"{email}"')
    body = _twenty_call("GET", f"/rest/people?filter={filt}&limit=1")
    items = (body.get("data") or {}).get("people") or []
    return items[0] if items else None


def patch_person(person_id: str, payload: dict) -> dict:
    body = _twenty_call("PATCH", f"/rest/people/{person_id}", payload)
    return (body.get("data") or {}).get("updatePerson") or {}


# ---------- Path-secret verification ----------

def verify_path_secret(secret_from_url: str) -> bool:
    """Constant-time compare the URL-path secret against the env value.

    The webhook URL is structured as `/webhooks/smartlead/wd/{secret}`; FastAPI
    extracts {secret} and passes it here. Smartlead's webhook system doesn't
    support HMAC, so URL secrecy is the auth model (equivalent to API-key).
    """
    expected = os.environ.get("SMARTLEAD_WD_WEBHOOK_PATH_SECRET", "")
    if not expected:
        # Fail-closed: if no secret configured, reject all webhooks
        return False
    if not secret_from_url:
        return False
    return hmac.compare_digest(secret_from_url, expected)


# ---------- Event-type handlers ----------

# Smartlead webhook event types we suppress on (any of these = do-not-contact)
SUPPRESS_EVENTS = {"lead_unsubscribed", "lead_bounced"}
# lead_replied is logged but does NOT suppress — replies are a SIGNAL, not opt-out
LOG_EVENTS = {"lead_replied"}


def _suppress_in_twenty(email: str, reason: str) -> str:
    """Find person in Twenty WD by email and flag do-not-contact.

    Twenty doesn't ship a `doNotContact` field by default. We use the existing
    `lastLeadStatus` field (or whatever Michael's WD workspace has) by setting
    it to a sentinel value the marketing pipeline filters on. If the field
    doesn't exist, the PATCH returns 400 and we log + 200 anyway so Smartlead
    doesn't retry.

    Returns one of: "suppressed", "not_found", "patch_failed"
    """
    try:
        person = find_person_by_email(email)
    except HTTPException as e:
        logger.error(f"twenty find {email}: {e.detail}")
        return "patch_failed"
    if not person:
        logger.info(f"twenty no match for {email} (smartlead {reason})")
        return "not_found"
    try:
        # Use a field every Twenty workspace has: jobTitle. We append the
        # suppression sentinel rather than overwriting, so existing jobTitle
        # is preserved. Marketing-side filters check for the substring.
        # SAFER ALTERNATIVE: add a custom `doNotContact` boolean field to
        # Twenty WD workspace via UI, then patch that instead. Recommend
        # doing that as a follow-up.
        existing_title = person.get("jobTitle") or ""
        sentinel = "[DO-NOT-CONTACT]"
        if sentinel not in existing_title:
            new_title = f"{sentinel} {existing_title}".strip()
            patch_person(person["id"], {"jobTitle": new_title})
        logger.info(f"twenty suppressed {email} reason={reason}")
        return "suppressed"
    except HTTPException as e:
        logger.error(f"twenty patch {email}: {e.detail}")
        return "patch_failed"


# ---------- Main handler ----------

async def handle_webhook(request: Request, secret_from_url: str) -> Dict[str, Any]:
    """Entry point called from app.py FastAPI route.

    secret_from_url comes from FastAPI's path parameter — see the
    `/webhooks/smartlead/wd/{secret}` route definition in app.py.
    """
    if not verify_path_secret(secret_from_url):
        raise HTTPException(status_code=401, detail="invalid path secret")

    raw = await request.body()
    try:
        payload = json.loads(raw.decode() or "{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid json")

    event_type = (payload.get("event_type") or payload.get("event") or "").lower()
    # Smartlead payload shape varies by event. Email is typically at one of:
    #   payload.lead.email
    #   payload.email
    #   payload.lead_email
    email = (
        ((payload.get("lead") or {}).get("email"))
        or payload.get("email")
        or payload.get("lead_email")
        or ""
    ).strip().lower()

    if not email:
        # Log + 200 so Smartlead doesn't retry. Logged for audit.
        logger.warning(f"smartlead webhook {event_type} with no email; payload keys={list(payload.keys())[:8]}")
        return {"ok": True, "action": "no_email_logged"}

    if event_type in SUPPRESS_EVENTS:
        outcome = _suppress_in_twenty(email, event_type)
        return {"ok": True, "event": event_type, "email": email, "outcome": outcome}

    if event_type in LOG_EVENTS:
        logger.info(f"smartlead reply from {email}")
        return {"ok": True, "event": event_type, "email": email, "outcome": "logged_no_suppress"}

    # Unknown event type — log + 200
    logger.info(f"smartlead webhook unknown event_type={event_type}")
    return {"ok": True, "event": event_type, "outcome": "unhandled_event_type"}
