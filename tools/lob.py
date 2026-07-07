"""tools/lob.py -- Lob direct-mail adapter (item 5 of the intent-workflow B&T flag).

Lob is the postcard/letter/USPS-integrated direct-mail API. Panda's brand.yaml
lists direct_mail as the primary channel (spec section 11 reference config);
this adapter is what turns a scored entity into a physical postcard in a
recipient's mailbox 5-8 business days later.

Env: LOB_API_KEY (Doppler paperclip/prd). Lob uses HTTP Basic with the key
as the username (no password), which is unusual but well documented.
Falls back to LOB_TEST_KEY when LOB_ENV=test so scripts can exercise the
API against Lob's sandbox without touching production.

Structural pattern mirrors tools/loops.py exactly:
  - _api_key_for() + _headers() + _lob_request() the low-level trio
  - Never raises; returns dict on success or "ERROR: ..." string on failure
  - Retries via requests.RequestException catch; caller decides to loop

Non-goals for v1:
  - No letter / check / self-mailer builders. Postcards are Panda's only
    format today; the runner + brand.yaml don't need more.
  - No CASS/NCOA validate as a separate call. Lob does it inline on send
    (documented behavior). If NCOA fails, the send API returns a 422 with a
    clear reason; the adapter surfaces that verbatim.
  - Address-book / template management. Postcard front/back come from
    brand.yaml (URLs to pre-uploaded PNGs); template IDs land when Panda's
    creative rotation goes multi-variant (future).

Delivery-status webhook: Lob POSTs to a URL we register on the account
(one-time setup). Our unified inbound endpoint at
POST /webhooks/intent-inbound/<secret> already accepts channel=direct_mail
+ response_type=qr_scan (when the postcard's QR fires) or bounce (when USPS
returns undeliverable). So this adapter's OUTBOUND path is what's here;
INBOUND is handled by services/intent_inbound.py already.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.lob.com/v1"
DEFAULT_TIMEOUT = 30


def _api_key_for(business_key: str = "panda") -> Optional[str]:
    """Look up the Lob key. LOB_ENV=test uses the sandbox key; anything else
    (including unset) uses the live key. Per-brand keys are not planned --
    Lob accounts are single-tenant."""
    env = (os.getenv("LOB_ENV") or "").strip().lower()
    if env == "test":
        return (os.getenv("LOB_TEST_KEY") or "").strip() or None
    return (os.getenv("LOB_API_KEY") or "").strip() or None


def _headers(api_key: str) -> Dict[str, str]:
    """Lob uses HTTP Basic with the key as the username, empty password.
    Encode manually so we do not depend on requests' auth= plumbing (some
    proxies strip Authorization if it looks off-shape)."""
    creds = base64.b64encode(f"{api_key}:".encode("utf-8")).decode("ascii")
    return {
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/x-www-form-urlencoded",
        # Lob's Idempotency-Key header supports up to 256 chars.
    }


def lob_ready(business_key: str = "panda") -> bool:
    """True iff a Lob key is set (live OR sandbox depending on LOB_ENV)."""
    return bool(_api_key_for(business_key))


def _lob_request(
    method: str,
    path: str,
    business_key: str = "panda",
    *,
    params: Optional[Dict[str, Any]] = None,
    form: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any] | str:
    """Low-level Lob HTTP. Returns parsed JSON on success or an "ERROR: ..."
    string on failure. Never raises.

    Lob's API is form-encoded (application/x-www-form-urlencoded), not JSON,
    which matters for lists (front, back, metadata[...]) that use bracket
    notation. We pass the dict through requests' `data=` parameter which
    handles the encoding.
    """
    api_key = _api_key_for(business_key)
    if not api_key:
        env_hint = "LOB_TEST_KEY" if (os.getenv("LOB_ENV", "").lower() == "test") else "LOB_API_KEY"
        return f"ERROR: {env_hint} not set. Add to Doppler paperclip/prd."

    url = f"{BASE_URL}/{path.lstrip('/')}"
    headers = _headers(api_key)
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key[:256]

    try:
        resp = requests.request(
            method, url, headers=headers,
            params=params or {}, data=form,
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.exceptions.Timeout:
        return f"ERROR: Lob timeout on {method} {path} (>{DEFAULT_TIMEOUT}s)"
    except requests.exceptions.RequestException as e:
        return f"ERROR: Lob request failed on {method} {path}: {type(e).__name__}: {e}"

    if resp.status_code in (401, 403):
        return f"ERROR: Lob rejected the API key (HTTP {resp.status_code}). Verify LOB_API_KEY."
    if resp.status_code == 422:
        # Address validation / NCOA failure lands here with a JSON body that
        # names the field; surface verbatim so the caller can act.
        try:
            body = resp.json()
        except ValueError:
            body = {"raw": resp.text[:300]}
        return f"ERROR: Lob 422 (validation): {json.dumps(body)[:500]}"
    if resp.status_code == 429:
        return "ERROR: Lob rate limit (429). Back off and retry."
    if resp.status_code >= 400:
        return f"ERROR: Lob HTTP {resp.status_code} on {method} {path}: {resp.text[:300]}"
    if not resp.content:
        return {"ok": True, "status": resp.status_code}
    try:
        return resp.json()
    except ValueError:
        return f"ERROR: Lob returned non-JSON on {method} {path}: {resp.text[:300]}"


# --------------------------------------------------------------------------- #
# Public API -- postcard send
# --------------------------------------------------------------------------- #


def create_address(
    name: str,
    address_line1: str,
    address_city: str,
    address_state: str,
    address_zip: str,
    address_line2: str = "",
    address_country: str = "US",
    business_key: str = "panda",
) -> Dict[str, Any] | str:
    """Create a Lob address (used for `from` on a postcard, and optionally for
    `to` when we want an id we can reference across sends)."""
    form = {
        "name": name,
        "address_line1": address_line1,
        "address_line2": address_line2,
        "address_city":  address_city,
        "address_state": address_state,
        "address_zip":   address_zip,
        "address_country": address_country,
    }
    return _lob_request("POST", "/addresses", business_key, form={k: v for k, v in form.items() if v})


def send_postcard(
    *,
    to: Dict[str, str],
    from_address: Dict[str, str] | str,
    front: str,
    back: str,
    size: str = "4x6",
    description: str = "",
    metadata: Optional[Dict[str, str]] = None,
    idempotency_key: Optional[str] = None,
    business_key: str = "panda",
) -> Dict[str, Any] | str:
    """Send a postcard.

    to: dict with the same fields as create_address (name + address_*), OR an
        object id string for a previously-created address.
    from_address: same shape as `to`, OR a pre-created Lob address id.
    front / back: URLs to the pre-rendered PNGs (Lob v1 supports HTML too but
        pre-rendered PNGs are more predictable). 4x6 postcards use 1875x1275
        px @ 300 DPI.
    size: Lob's postcard sizes -- "4x6" (default), "6x9", "6x11".
    metadata: up to 20 arbitrary key/value pairs; Lob echoes them back on the
        delivery-status webhook so we can correlate to our scored entity.
        Convention: {"brand": ..., "person_id": ..., "config_version": ...,
                     "idempotency_key": ...}.
    idempotency_key: passed as Idempotency-Key header. Deduplicates retries
        server-side; Lob returns the original postcard on collision instead
        of double-charging.
    """
    form: Dict[str, Any] = {"front": front, "back": back, "size": size}
    if description:
        form["description"] = description

    # to / from can each be either an id string OR an inline address (which
    # Lob expects as bracketed keys: to[name], to[address_line1], etc.).
    if isinstance(to, str):
        form["to"] = to
    else:
        for k, v in to.items():
            if v is not None and v != "":
                form[f"to[{k}]"] = v
    if isinstance(from_address, str):
        form["from"] = from_address
    else:
        for k, v in from_address.items():
            if v is not None and v != "":
                form[f"from[{k}]"] = v

    if metadata:
        for k, v in metadata.items():
            form[f"metadata[{k}]"] = str(v)[:500]

    return _lob_request(
        "POST", "/postcards", business_key,
        form=form, idempotency_key=idempotency_key,
    )


def get_postcard(postcard_id: str, business_key: str = "panda") -> Dict[str, Any] | str:
    return _lob_request("GET", f"/postcards/{postcard_id}", business_key)


def list_postcards(
    limit: int = 100,
    business_key: str = "panda",
    metadata_filter: Optional[Dict[str, str]] = None,
) -> Dict[str, Any] | str:
    """List recent postcards. Supports metadata filtering via query params
    (metadata[brand]=panda etc.)."""
    params: Dict[str, Any] = {"limit": max(1, min(100, limit))}
    if metadata_filter:
        for k, v in metadata_filter.items():
            params[f"metadata[{k}]"] = v
    return _lob_request("GET", "/postcards", business_key, params=params)


def cancel_postcard(postcard_id: str, business_key: str = "panda") -> Dict[str, Any] | str:
    """Cancel a postcard BEFORE it hits the send_date (Lob prints in daily
    batches so there's a window). After render, cancellation returns 422."""
    return _lob_request("DELETE", f"/postcards/{postcard_id}", business_key)


# --------------------------------------------------------------------------- #
# Webhook helpers -- Lob POSTs delivery-status updates to our registered URL.
# The unified inbound endpoint handles the parse; this helper normalizes the
# Lob payload into the IntentInboundEvent contract shape.
# --------------------------------------------------------------------------- #


def lob_event_to_intent_inbound(lob_event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Convert a Lob delivery-status webhook payload into the unified
    IntentInboundEvent JSON shape. Returns None if this Lob event does not
    map to a downstream response (e.g. internal-state events we ignore).

    Lob event types -> our response_type mapping:
      postcard.rendered_pdf     -> None (internal; ignore)
      postcard.deleted          -> None (cancel path, we don't need to
                                    surface it to the workflow -- the audit
                                    table already carries the cancellation)
      postcard.in_transit       -> None (informational)
      postcard.processed_for_delivery -> None (informational)
      postcard.re-routed        -> None (informational)
      postcard.returned_to_sender -> "bounce"
      postcard.delivered        -> None by default (delivery != engagement).
                                    Set LOB_DELIVERED_TO_INBOUND=1 in env to
                                    treat delivered as an inbound signal (rare
                                    use case; most brands wait for a QR scan
                                    which comes through a different path).
    """
    # Lob wraps event_type as {"id": "postcard.returned_to_sender", ...} in
    # newer versions and as a bare string in older ones; accept both.
    raw_type = lob_event.get("event_type")
    if isinstance(raw_type, dict):
        ev_type = str(raw_type.get("id") or "").lower()
    else:
        ev_type = str(raw_type or "").lower()

    if ev_type == "postcard.returned_to_sender":
        response_type = "bounce"
    elif ev_type == "postcard.delivered" and os.getenv("LOB_DELIVERED_TO_INBOUND", "").strip() == "1":
        response_type = "form_submit"  # closest neutral proxy; caller can override
    else:
        return None

    body = lob_event.get("body") or {}
    meta = body.get("metadata") or {}
    person_ref: Dict[str, Any] = {}
    if meta.get("person_id"):
        person_ref["external_id"] = meta["person_id"]
    to = body.get("to") or {}
    if to.get("email"):
        person_ref["email"] = to["email"]
    if to.get("phone_number"):
        person_ref["phone"] = to["phone_number"]
    if not person_ref:
        person_ref["external_id"] = body.get("id") or lob_event.get("id") or "unknown"

    ts = (
        lob_event.get("date_created")
        or body.get("date_modified")
        or body.get("date_created")
    )
    return {
        "brand": meta.get("brand", "panda"),
        "person_ref": person_ref,
        "channel": "direct_mail",
        "response_type": response_type,
        "raw_body": lob_event,
        "timestamp": ts,
        "idempotency_key": meta.get("idempotency_key") or body.get("id"),
    }


__all__ = [
    "lob_ready",
    "create_address",
    "send_postcard",
    "get_postcard",
    "list_postcards",
    "cancel_postcard",
    "lob_event_to_intent_inbound",
]
