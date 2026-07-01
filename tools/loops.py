"""Loops (loops.so) connector — Worship Digital's email platform.

Unlike Klaviyo flows (UI-only) and GHL blog (PUT ignores body), Loops exposes
email-message CONTENT editing via API, so copy fixes (e.g. em-dash cleanup) can
be pushed on-platform. Covers the editable surface:

    campaigns      GET  /v1/campaigns              list
                   GET  /v1/campaigns/{id}         get (incl. its email messages)
    email messages GET  /v1/email-messages/{id}    get (content + revisionId)
                   POST /v1/email-messages/{id}    update content (optimistic lock)
    contacts       GET  /v1/contacts/find          find by email
                   PUT  /v1/contacts/update        upsert (create-or-update by email)
    events         POST /v1/events                 track an event / touch against a contact
    transactional  POST /v1/transactional          send
    api key        GET  /v1/api-key                validate

Updating a message is optimistic-concurrency: GET it for the current revisionId
+ content, then POST back with `expectedRevisionId`. `content` is Loops "LMX"
markup (em-dash stripping is a plain character substitution on it).

Env (per-brand suffix, falls back to unsuffixed): LOOPS_API_KEY_WD or LOOPS_API_KEY.
Loops = WD, so business_key defaults to "wd".
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

import requests

try:
    from crewai.tools import tool
except Exception:  # allow import + script use without crewai installed
    def tool(_name):  # type: ignore
        def _wrap(fn):
            return fn
        return _wrap

BASE_URL = "https://app.loops.so/api/v1"
DEFAULT_TIMEOUT = 30
_REV_KEYS = ("revisionId", "expectedRevisionId", "revision_id", "latestRevisionId")


def _suffix(business_key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (business_key or "").strip().lower()).upper()


def _api_key_for(business_key: str = "wd") -> Optional[str]:
    suffix = _suffix(business_key)
    val = ""
    if suffix:
        val = (os.environ.get(f"LOOPS_API_KEY_{suffix}") or "").strip()
    return val or (os.environ.get("LOOPS_API_KEY") or "").strip() or None


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _truncate_json(obj: Any, max_chars: int = 8000) -> str:
    out = json.dumps(obj, indent=2, default=str)
    return out if len(out) <= max_chars else out[:max_chars] + "\n\n[...truncated...]"


def _loops_request(
    method: str,
    path: str,
    business_key: str = "wd",
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any] | str:
    """Low-level Loops HTTP. Returns parsed JSON on success or an error string.
    Never raises."""
    api_key = _api_key_for(business_key)
    if not api_key:
        return (f"ERROR: LOOPS_API_KEY_{_suffix(business_key)} (or LOOPS_API_KEY) "
                "not set. Add the Worship Digital Loops API key to paperclip/.env "
                "/ Doppler.")
    url = f"{BASE_URL}/{path.lstrip('/')}"
    try:
        resp = requests.request(method, url, headers=_headers(api_key),
                                params=params or {}, json=json_body, timeout=DEFAULT_TIMEOUT)
    except requests.exceptions.Timeout:
        return f"ERROR: Loops timeout on {method} {path} (>{DEFAULT_TIMEOUT}s)"
    except requests.exceptions.RequestException as e:
        return f"ERROR: Loops request failed on {method} {path}: {type(e).__name__}: {e}"
    if resp.status_code in (401, 403):
        return (f"ERROR: Loops rejected the API key (HTTP {resp.status_code}). "
                f"Verify LOOPS_API_KEY_{_suffix(business_key)}.")
    if resp.status_code == 409:
        return ("ERROR: Loops revision conflict (409) — the message changed since "
                "you read it. Re-GET for the current revisionId and retry.")
    if resp.status_code == 429:
        return "ERROR: Loops rate limit (429). Wait and retry."
    if resp.status_code >= 400:
        return f"ERROR: Loops HTTP {resp.status_code} on {method} {path}: {resp.text[:300]}"
    if resp.status_code == 204 or not resp.content:
        return {"ok": True, "status": resp.status_code}
    try:
        return resp.json()
    except ValueError:
        return f"ERROR: Loops returned non-JSON on {method} {path}: {resp.text[:300]}"


# --------------------------------------------------------------------------- #
# Raw API (return dict|str) — use these from scripts.
# --------------------------------------------------------------------------- #
def validate_key(business_key: str = "wd") -> dict[str, Any] | str:
    return _loops_request("GET", "/api-key", business_key)


def list_campaigns(business_key: str = "wd") -> dict[str, Any] | str:
    return _loops_request("GET", "/campaigns", business_key)


def get_campaign(campaign_id: str, business_key: str = "wd") -> dict[str, Any] | str:
    return _loops_request("GET", f"/campaigns/{campaign_id}", business_key)


def get_email_message(email_message_id: str, business_key: str = "wd") -> dict[str, Any] | str:
    return _loops_request("GET", f"/email-messages/{email_message_id}", business_key)


def _revision_of(msg: dict[str, Any]) -> Optional[str]:
    for k in _REV_KEYS:
        if msg.get(k):
            return str(msg[k])
    rev = msg.get("revision")
    if isinstance(rev, dict) and rev.get("id"):
        return str(rev["id"])
    return None


def update_email_message(
    email_message_id: str,
    *,
    content: Optional[str] = None,
    subject: Optional[str] = None,
    preview_text: Optional[str] = None,
    sender: Optional[str] = None,
    expected_revision_id: Optional[str] = None,
    business_key: str = "wd",
) -> dict[str, Any] | str:
    """Update a Loops email message. If expected_revision_id is omitted, GETs the
    message first to read the current revisionId (optimistic-concurrency lock)."""
    if expected_revision_id is None:
        cur = get_email_message(email_message_id, business_key)
        if isinstance(cur, str):
            return cur
        expected_revision_id = _revision_of(cur)
        if not expected_revision_id:
            return ("ERROR: could not find a revisionId on the email message; "
                    f"keys present: {sorted(cur.keys())}")
    body: dict[str, Any] = {"expectedRevisionId": expected_revision_id}
    if content is not None:
        body["content"] = content
    if subject is not None:
        body["subject"] = subject
    if preview_text is not None:
        body["previewText"] = preview_text
    if sender is not None:
        body["sender"] = sender
    return _loops_request("POST", f"/email-messages/{email_message_id}",
                          business_key, json_body=body)


def find_contact(email: str, business_key: str = "wd") -> dict[str, Any] | str:
    return _loops_request("GET", "/contacts/find", business_key, params={"email": email})


def create_or_update_contact(
    email: str,
    properties: dict[str, Any] | None = None,
    mailing_lists: dict[str, bool] | None = None,
    business_key: str = "wd",
) -> dict[str, Any] | str:
    """Upsert a Loops contact (create if absent, update if present), keyed by email.

    Loops PUT /v1/contacts/update is an upsert: it creates the contact when no
    match exists and updates it otherwise. `properties` carries standard fields
    (firstName, lastName, source, subscribed, userGroup, ...) AND custom
    properties as top-level attributes — Loops auto-creates unknown custom
    properties on first use. `mailing_lists` maps list-id -> bool subscription.

    Returns Loops' JSON, e.g. {"success": true, "id": "<contactId>"}.
    """
    if not (email or "").strip():
        return "ERROR: create_or_update_contact requires a non-empty email."
    body: dict[str, Any] = {"email": email}
    if properties:
        # Loops takes contact properties as TOP-LEVEL attributes (not nested).
        body.update(properties)
    if mailing_lists:
        body["mailingLists"] = mailing_lists
    return _loops_request("PUT", "/contacts/update", business_key, json_body=body)


def set_contact_properties(
    email: str,
    properties: dict[str, Any],
    business_key: str = "wd",
) -> dict[str, Any] | str:
    """Property-only update: thin wrapper over the contacts upsert.

    Use for deal-tracking fields (e.g. dealStage, dealValue, lastTouchAt). Sending
    a property value of None resets it in Loops.
    """
    if not properties:
        return "ERROR: set_contact_properties requires at least one property."
    return create_or_update_contact(email, properties=properties, business_key=business_key)


def track_event(
    email: str,
    event_name: str,
    event_properties: dict[str, Any] | None = None,
    contact_properties: dict[str, Any] | None = None,
    business_key: str = "wd",
) -> dict[str, Any] | str:
    """Log an event ("touch": email sent, call made, stage change) against a contact.

    Loops POST /v1/events. Payload: `email`, `eventName`, optional `eventProperties`
    (event metadata), and contact properties which Loops reads as TOP-LEVEL
    attributes (used to update the contact at the same time as the event fires) —
    so `contact_properties` are merged into the top-level body, not nested.

    Returns Loops' JSON, e.g. {"success": true}.
    """
    if not (email or "").strip():
        return "ERROR: track_event requires a non-empty email."
    if not (event_name or "").strip():
        return "ERROR: track_event requires a non-empty event_name."
    body: dict[str, Any] = {"email": email, "eventName": event_name}
    if event_properties:
        body["eventProperties"] = event_properties
    if contact_properties:
        # Contact properties on an event are TOP-LEVEL attributes in Loops' API.
        body.update(contact_properties)
    return _loops_request("POST", "/events", business_key, json_body=body)


def send_transactional(
    transactional_id: str,
    email: str,
    data_variables: dict[str, Any] | None = None,
    business_key: str = "wd",
) -> dict[str, Any] | str:
    body: dict[str, Any] = {"transactionalId": transactional_id, "email": email}
    if data_variables:
        body["dataVariables"] = data_variables
    return _loops_request("POST", "/transactional", business_key, json_body=body)


def loops_status() -> dict[str, Any]:
    """Observability: is a key configured, and does it validate?"""
    configured = bool(_api_key_for("wd"))
    if not configured:
        return {"configured": False, "valid": False,
                "detail": "LOOPS_API_KEY_WD / LOOPS_API_KEY not set"}
    res = validate_key("wd")
    return {"configured": True, "valid": not isinstance(res, str),
            "detail": res if isinstance(res, str) else "ok"}


# --------------------------------------------------------------------------- #
# CrewAI agent surface (return strings).
# --------------------------------------------------------------------------- #
@tool("Loops: list campaigns")
def loops_list_campaigns(business_key: str = "wd") -> str:
    """List Loops marketing campaigns for the brand (default Worship Digital)."""
    return _truncate_json(list_campaigns(business_key))


@tool("Loops: get email message")
def loops_get_email_message(email_message_id: str, business_key: str = "wd") -> str:
    """Get a Loops email message (subject, preview, content/LMX, revisionId)."""
    return _truncate_json(get_email_message(email_message_id, business_key))


@tool("Loops: update email message content")
def loops_update_email_message(email_message_id: str, content: str,
                               business_key: str = "wd") -> str:
    """Update a Loops email message's content (LMX markup). Handles the
    optimistic-concurrency revisionId automatically."""
    return _truncate_json(update_email_message(email_message_id, content=content,
                                               business_key=business_key))


@tool("Loops: send transactional email")
def loops_send_transactional(transactional_id: str, email: str,
                             business_key: str = "wd") -> str:
    """Send a Loops transactional email by transactionalId to a recipient."""
    return _truncate_json(send_transactional(transactional_id, email,
                                             business_key=business_key))


@tool("Loops: upsert contact")
def loops_upsert_contact(email: str, properties: dict[str, Any] | None = None,
                         mailing_lists: dict[str, bool] | None = None,
                         business_key: str = "wd") -> str:
    """Create-or-update (upsert) a Loops contact by email. `properties` holds
    standard + custom contact fields (top-level; custom ones auto-create). Use for
    Sales Desk deal tracking — e.g. properties={"dealStage": "qualified"}."""
    return _truncate_json(create_or_update_contact(email, properties=properties,
                                                   mailing_lists=mailing_lists,
                                                   business_key=business_key))


@tool("Loops: set contact properties")
def loops_set_contact_properties(email: str, properties: dict[str, Any],
                                 business_key: str = "wd") -> str:
    """Update deal-tracking (or any) properties on a Loops contact by email
    (property-only upsert). Send a value of null to reset a property."""
    return _truncate_json(set_contact_properties(email, properties,
                                                 business_key=business_key))


@tool("Loops: track event")
def loops_track_event(email: str, event_name: str,
                      event_properties: dict[str, Any] | None = None,
                      contact_properties: dict[str, Any] | None = None,
                      business_key: str = "wd") -> str:
    """Log a touch/event against a Loops contact (email sent, call made, stage
    change) via POST /v1/events. `event_properties` = event metadata;
    `contact_properties` also update the contact. For Sales Desk deal tracking."""
    return _truncate_json(track_event(email, event_name,
                                      event_properties=event_properties,
                                      contact_properties=contact_properties,
                                      business_key=business_key))


if __name__ == "__main__":
    print(json.dumps(loops_status(), indent=2))
