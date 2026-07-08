"""services/datamoon_visitor_id.py -- DataMoon Visitor-ID webhook normalizer.

Per CRO's flag addendum to intent-workflow item 4 (revenue_state.md 2026-07-07):

  The unified inbound webhook must ALSO accept the DataMoon/Datashopper
  Visitor-Identification payload as a first-party S1 signal source
  (website de-anon: name/email/phone/location/landing_page, POSTed
  per-minute). Twenty is NOT a native DataMoon CRM connector; the
  Webhook connector is the ONLY path to Twenty. Michael does the
  DataMoon-side config; B&T exposes the endpoint.

This module owns three things:
  1. Domain -> brand routing (which Twenty workspace a visitor lands in
     based on which brand site fired the identification).
  2. Landing-page -> heat rating (which subtype label goes on the Signal
     so the closer knows how hot the visitor is at a glance).
  3. Normalization from DataMoon's payload shape into our unified
     IntentInboundEvent contract, so the same handle_event() path used
     by Instantly replies + Meta lead-forms works verbatim.

The endpoint lives in app.py at POST /webhooks/datamoon-visitor-id/{secret}
(reuses INTENT_INBOUND_WEBHOOK_PATH_SECRET; one secret to manage).

Payload shape is flexible on purpose. DataMoon's Webhook connector lets
you configure the field map, so we accept several common shapes and log
the raw payload whenever a required field is missing so Michael can adjust
the DataMoon-side mapping.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain -> brand routing
#
# Landing page URL's host determines which brand's Twenty workspace this
# visitor lands in. The map covers the six brand configs we have today;
# an unknown host falls back to a "brand" query parameter if DataMoon's
# Webhook connector was configured with it, otherwise emits a clear error.
# ---------------------------------------------------------------------------


_DOMAIN_TO_BRAND: Dict[str, str] = {
    # AvI
    "automotiveintelligence.io":          "avi",
    "www.automotiveintelligence.io":      "avi",
    # WD
    "worshipdigital.co":                  "wd",
    "www.worshipdigital.co":              "wd",
    # AIPG
    "theaiphoneguy.com":                  "aipg",
    "www.theaiphoneguy.com":              "aipg",
    "theaiphoneguy.ai":                   "aipg",
    "www.theaiphoneguy.ai":               "aipg",
    # Book'd
    "bookd.cx":                           "bookd",
    "www.bookd.cx":                       "bookd",
    "crm.bookd.cx":                       "bookd",
    # P&P
    "paperandpurpose.co":                 "pp",
    "www.paperandpurpose.co":             "pp",
    "getpaperandpurpose.com":             "pp",
    # BAE
    "buildagentempire.com":               "bae",
    "www.buildagentempire.com":           "bae",
}


def _extract_brand(landing_page_url: str, payload: Dict[str, Any]) -> Optional[str]:
    """Resolve brand from (in priority):
      1. explicit `brand` field in the payload (DataMoon field-map override)
      2. `brand` query parameter on the landing page URL
      3. domain -> brand map
    """
    explicit = (payload.get("brand") or "").strip().lower()
    if explicit:
        return explicit
    if not landing_page_url:
        return None
    try:
        parsed = urlparse(landing_page_url if "://" in landing_page_url else "https://" + landing_page_url)
    except ValueError:
        return None
    # Query-param override (Michael can add ?brand=avi to any short URL that
    # DataMoon tracks, without touching the map).
    if parsed.query:
        for pair in parsed.query.split("&"):
            if "=" not in pair:
                continue
            k, v = pair.split("=", 1)
            if k.strip().lower() == "brand" and v.strip():
                return v.strip().lower()
    host = (parsed.hostname or "").lower()
    return _DOMAIN_TO_BRAND.get(host)


# ---------------------------------------------------------------------------
# Landing-page -> heat rating
#
# Per CRO's flag: "landing_page = heat, /diagnostic + /contact = hot".
# The heat label lands on the Signal's `subtype` field so the closer can
# filter Twenty by "hot visitors from AvI in the last 24 hours" without
# recomputing anything.
# ---------------------------------------------------------------------------

_HOT_PATH_MARKERS = (
    "/diagnostic", "/contact", "/demo", "/book", "/call", "/quote",
    "/schedule", "/consult", "/talk", "/strategy-call", "/pricing",
    "/checkout", "/get-started", "/apply",
)
_WARM_PATH_MARKERS = (
    "/blog/", "/case-studies", "/case-study", "/services", "/solutions",
    "/how", "/faq", "/reviews", "/testimonials", "/about",
)


def _heat_from_landing_page(landing_page_url: str) -> str:
    """Return 'hot' / 'warm' / 'cold' based on the URL path.

    Priority-ordered: any HOT marker beats any WARM marker (a page like
    /blog/why-book-a-demo/ still counts as hot if it also matches /book).
    """
    if not landing_page_url:
        return "cold"
    try:
        parsed = urlparse(landing_page_url if "://" in landing_page_url else "https://" + landing_page_url)
    except ValueError:
        return "cold"
    path = (parsed.path or "/").lower()
    for m in _HOT_PATH_MARKERS:
        if m in path:
            return "hot"
    for m in _WARM_PATH_MARKERS:
        if m in path:
            return "warm"
    if path in ("", "/"):
        # Homepage is warm-ish; someone identified from the home page has
        # intent but not urgent enough to be "hot."
        return "warm"
    return "cold"


# ---------------------------------------------------------------------------
# Person-ref extraction
#
# DataMoon's Webhook payload varies by tenant field-map, so we look at several
# common shapes: nested under `person`, nested under `visitor`, or top-level.
# ---------------------------------------------------------------------------


def _first(*candidates: Any) -> Optional[str]:
    """Return the first non-empty string from candidates."""
    for c in candidates:
        if c is None:
            continue
        s = str(c).strip()
        if s:
            return s
    return None


def _extract_person_ref(payload: Dict[str, Any]) -> Dict[str, str]:
    """Build a PersonRef-shaped dict from the raw DataMoon payload."""
    person = payload.get("person") or payload.get("visitor") or {}
    company = payload.get("company") or {}

    email = _first(
        person.get("email") if isinstance(person, dict) else None,
        payload.get("email"),
        payload.get("email_address"),
    )
    phone = _first(
        person.get("phone") if isinstance(person, dict) else None,
        person.get("phone_number") if isinstance(person, dict) else None,
        payload.get("phone"),
        payload.get("phone_number"),
    )
    external_id = _first(
        payload.get("visitor_id"),
        payload.get("id"),
        payload.get("session_id"),
        person.get("id") if isinstance(person, dict) else None,
    )

    out: Dict[str, str] = {}
    if email:
        out["email"] = email.lower()
    if phone:
        out["phone"] = phone
    if external_id:
        out["external_id"] = external_id
    return out


def _extract_timestamp(payload: Dict[str, Any]) -> datetime:
    """Best-effort ISO timestamp extraction. Falls back to now."""
    raw = _first(
        payload.get("identified_at"),
        payload.get("timestamp"),
        payload.get("occurred_at"),
        payload.get("event_time"),
        payload.get("created_at"),
    )
    if raw:
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Public entry -- one function called by the endpoint
# ---------------------------------------------------------------------------


def datamoon_payload_to_event(raw_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return an IntentInboundEvent-shaped dict (ready for handle_event()) OR
    a dict {ok: false, ...} when the payload can't be normalized.

    The normalizer is intentionally permissive on shape: DataMoon's Webhook
    connector is configurable per-tenant, so we accept several common field
    layouts. What we absolutely require:
      - Some form of person identifier (email OR phone OR external_id)
      - A landing_page URL (for brand routing + heat)
    Missing either surfaces as a 400 with the raw payload for Michael to
    adjust the DataMoon-side field map.
    """
    if not isinstance(raw_payload, dict):
        return {"ok": False, "reason": f"expected dict, got {type(raw_payload).__name__}"}

    landing_page = _first(
        raw_payload.get("landing_page"),
        raw_payload.get("landing_url"),
        raw_payload.get("url"),
        raw_payload.get("page_url"),
    )
    if not landing_page:
        return {
            "ok": False,
            "reason": "landing_page missing; cannot route brand or rate heat",
            "hint": "Configure DataMoon field-map to include landing_page / landing_url / page_url",
        }

    brand = _extract_brand(landing_page, raw_payload)
    if not brand:
        try:
            host = urlparse(landing_page).hostname
        except ValueError:
            host = "?"
        return {
            "ok": False,
            "reason": f"could not resolve brand from landing_page host {host!r}",
            "hint": "Add domain to _DOMAIN_TO_BRAND or configure DataMoon field-map to send ?brand=<key>",
        }

    person_ref = _extract_person_ref(raw_payload)
    if not person_ref:
        return {
            "ok": False,
            "reason": "no person identifier (need email OR phone OR external_id / visitor_id)",
            "hint": "Verify DataMoon field-map includes at least one identifier",
        }

    heat = _heat_from_landing_page(landing_page)
    ts = _extract_timestamp(raw_payload)

    event: Dict[str, Any] = {
        "brand": brand,
        "person_ref": person_ref,
        "channel": "web_visitor",
        "response_type": "form_submit",  # closest existing enum for downstream triage
        "subtype": heat,                  # "hot" / "warm" / "cold"
        "raw_body": raw_payload,
        "timestamp": ts.isoformat(),
    }
    return {"ok": True, "event": event, "heat": heat, "landing_page": landing_page}


__all__ = [
    "datamoon_payload_to_event",
    "_extract_brand",
    "_extract_person_ref",
    "_heat_from_landing_page",
]
