"""services/intent_inbound.py -- Unified inbound webhook handler (item 4).

Item 4 of the B&T flag posted 2026-07-03 in avo-telemetry/revenue_state.md.
Complete spec: avo-telemetry/marketing_deliverables/intent_workflow_spec_v1_2026-07-03.md
section 5, line 130 (the unified inbound contract):

    All adapters POST the same JSON to one S6 endpoint:
        {brand, person_ref, channel, response_type, raw_body, timestamp}
    A postcard's QR scan and a cold-email reply arrive at S6 identical.

Design decisions locked at the 2026-07-03 checkpoint (Michael):
  Q4.1  Auth = path-secret ONLY (env INTENT_INBOUND_WEBHOOK_PATH_SECRET). Skip
        HMAC; 4 of 5 inbound sources do not support signing.
  Q4.2  person_ref = composite. Handler resolves in priority order:
        twenty_person_id -> email -> phone -> external_id -> CREATE new Person.
        At least one identifier required.
  Q4.3  response_type = 8-value enum (below). Extensible via optional `subtype`
        string.
  Q4.4  New endpoint `POST /webhooks/intent-inbound/{secret}` implements the
        unified contract. The existing `/webhooks/instantly` endpoint (which
        does its own GHL pipeline promotion) is kept intact + additionally
        forwards a normalized event into this pipeline as a shim.

What this module does today (item 4 scope):
  - Validate payload against IntentInboundEvent schema
  - Persist to Postgres audit table `intent_inbound_events` (idempotent)
  - Upsert Person in the brand's Twenty workspace (basic fields; the Signal
    custom object write lands with item 6 Twenty schema enforcement)
  - Return {ok, event_id, person_id, deduped}

What lands in later items:
  - item 5: channel adapters emitting into this endpoint (Meta lead-form, Lob,
    sheet-drop). Existing Instantly/Smartlead/Loops adapters get shimmed here.
  - item 6: Twenty schema enforcement + Signal custom-object writer + stage
    transitions
  - item 8: compliance hard-gate as precondition on the response side
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Unified contract (Q4.3 + Q4.4 lock)
# ---------------------------------------------------------------------------


ResponseType = Literal[
    "inbound_reply",     # email response (cold or warm)
    "form_submit",       # Meta lead-form, web form, landing-page submission
    "phone_ring",        # CallRail, receptionist bridge, GHL Voice AI
    "qr_scan",           # postcard QR / print-media scan
    "sms_reply",         # Twilio / GHL SMS inbound
    "meeting_booked",    # calendar event created
    "unsubscribe",       # opt-out, terminal state
    "bounce",            # hard bounce, terminal
]


ChannelName = Literal[
    "cold_email",
    "warm_email",
    "linkedin",
    "direct_mail",
    "meta_lead_ad",
    "sms",
    "inbound_call",
    "sheet_drop",
]


class PersonRef(BaseModel):
    """Composite identifier. At least one field required. Handler resolves in
    the priority set at the 2026-07-03 checkpoint:

        twenty_person_id -> email -> phone -> external_id -> CREATE new Person

    The composite shape lets Panda (direct-mail brand) send an inbound event
    with only a QR scan session id, and lets Instantly send an inbound with
    email only, and lets a receptionist send with phone only. All arrive at
    the same S6 endpoint identically.
    """
    model_config = ConfigDict(extra="forbid")

    twenty_person_id: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None  # E.164 preferred; handler does not normalize (yet)
    external_id: Optional[str] = None  # e.g. postcard QR session, form-fill id

    @model_validator(mode="after")
    def _at_least_one(self) -> "PersonRef":
        if not any([self.twenty_person_id, self.email, self.phone, self.external_id]):
            raise ValueError(
                "PersonRef must include at least one of: "
                "twenty_person_id, email, phone, external_id"
            )
        return self

    def resolution_key(self) -> str:
        """Priority-order string used for idempotency + resolution."""
        return (
            self.twenty_person_id
            or (self.email or "").lower()
            or self.phone
            or self.external_id
            or ""
        )


class IntentInboundEvent(BaseModel):
    """The unified S6 contract. Every adapter POSTs this shape."""
    model_config = ConfigDict(extra="forbid")

    brand: str  # matches config/brands/<brand>.yaml stem
    person_ref: PersonRef
    channel: ChannelName
    response_type: ResponseType
    raw_body: Dict[str, Any] = Field(default_factory=dict)  # verbatim source payload
    timestamp: datetime  # when the event OCCURRED (sender-provided; not received-at)
    # Optional subtype for the extensibility hook (Q4.3): e.g. response_type=
    # "form_submit" with subtype="quote_request" vs "newsletter_opt_in".
    subtype: Optional[str] = None
    # Optional explicit idempotency_key from the sender. If absent, handler
    # derives one deterministically from (brand, person_ref.resolution_key,
    # channel, response_type, timestamp).
    idempotency_key: Optional[str] = None

    @field_validator("brand")
    @classmethod
    def _brand_matches_config(cls, v: str) -> str:
        v = v.strip().lower()
        if not v:
            raise ValueError("brand cannot be empty")
        return v


# ---------------------------------------------------------------------------
# Brand -> Twenty workspace routing (mirrors services/ghl_voice_ai_bridge.py)
# ---------------------------------------------------------------------------


_BRAND_TO_TWENTY_KEY: Dict[str, Optional[str]] = {
    "avi":   "autointelligence",
    "wd":    "callingdigital",       # WD workspace
    "aipg":  None,                    # AIPG remains on GHL; Twenty write skipped
    "bookd": None,                    # held for Ryan sign-off, per bridge module
    "pp":    None,                    # P&P Twenty workspace not yet provisioned
    "panda": None,                    # Panda has no cold-email path; write when workspace lands
}


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def _derive_idempotency_key(event: IntentInboundEvent) -> str:
    """Deterministic key. Two identical events (same brand + person + channel +
    response_type + occurred-at) produce the same key. Callers may override
    via IntentInboundEvent.idempotency_key when they have a stronger natural
    key (e.g. an Instantly message id)."""
    if event.idempotency_key:
        return event.idempotency_key
    parts = "|".join([
        event.brand,
        event.person_ref.resolution_key(),
        event.channel,
        event.response_type,
        event.timestamp.astimezone(timezone.utc).isoformat(),
    ])
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Audit table (idempotent persistence)
# ---------------------------------------------------------------------------


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS intent_inbound_events (
    id                SERIAL PRIMARY KEY,
    idempotency_key   TEXT UNIQUE NOT NULL,
    brand             TEXT NOT NULL,
    channel           TEXT NOT NULL,
    response_type     TEXT NOT NULL,
    subtype           TEXT,
    twenty_person_id  TEXT,
    email             TEXT,
    phone             TEXT,
    external_id       TEXT,
    occurred_at       TIMESTAMPTZ NOT NULL,
    received_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    twenty_status     TEXT,           -- created / duplicate / skipped / error
    twenty_person_written_id TEXT,    -- Twenty Person id after upsert (v1)
    raw_body          JSONB
);

CREATE INDEX IF NOT EXISTS ix_intent_inbound_events_brand_channel_occurred
    ON intent_inbound_events (brand, channel, occurred_at DESC);
"""


def _persist_audit(event: IntentInboundEvent, idem: str, twenty_result: Dict[str, Any]) -> Dict[str, Any]:
    """Insert-or-noop against the audit table. Returns {inserted, deduped}.
    Uses ON CONFLICT (idempotency_key) DO NOTHING so duplicate sends are safe.
    """
    try:
        from services.database import execute_query
        execute_query(_CREATE_TABLE_SQL)
        # ON CONFLICT ... RETURNING id: on new insert we get the id; on conflict
        # we get zero rows so a second query looks it up. For our purposes we
        # just distinguish "first time" vs "already seen."
        row = execute_query(
            """
            INSERT INTO intent_inbound_events
                (idempotency_key, brand, channel, response_type, subtype,
                 twenty_person_id, email, phone, external_id,
                 occurred_at, twenty_status, twenty_person_written_id, raw_body)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING id
            """,
            (
                idem, event.brand, event.channel, event.response_type, event.subtype,
                event.person_ref.twenty_person_id, event.person_ref.email,
                event.person_ref.phone, event.person_ref.external_id,
                event.timestamp,
                twenty_result.get("status"),
                twenty_result.get("twenty_id") or None,
                json.dumps(event.raw_body, default=str),
            ),
            fetch=True,
        )
        inserted = bool(row)
        return {"inserted": inserted, "deduped": not inserted}
    except Exception as e:
        logger.exception("[intent-inbound] audit persist failed: %s", e)
        return {"inserted": False, "deduped": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Twenty write (basic Person upsert; Signal object writes land in item 6)
# ---------------------------------------------------------------------------


def _write_to_twenty(event: IntentInboundEvent, idem: str) -> Dict[str, Any]:
    """Upsert Person + write Signal row in the brand's Twenty workspace.

    Item 6 wiring: uses services/twenty_schema.upsert_person_with_score to
    handle the Person + custom-field stamp, then services/twenty_schema.
    create_signal_record to append the S1 event as its own row (Signal is a
    custom Twenty object per spec section 4; one row per intent event).

    For brands with no Twenty workspace mapped today (AIPG on GHL; Book'd/P&P/
    Panda pending), the handler skips both writes and returns status="skipped".
    The event still lands in the audit table for observability + later replay.

    Scoring is NOT computed here; the caller (a downstream S2 enrichment step
    or a scheduled re-scorer) writes score fields when they have the fit
    inputs. Item 4's minimum-viable path leaves score_snapshot empty on the
    initial insert; a follow-up updates the Person + adds a scored Signal row.
    """
    business_key = _BRAND_TO_TWENTY_KEY.get(event.brand)
    if not business_key:
        return {
            "status": "skipped",
            "reason": f"brand {event.brand!r} not routed to Twenty (mapping is None)",
        }
    p = {
        "business_name": "",
        "contact": "",
        "first_name": "",
        "last_name": "",
        "phone":  event.person_ref.phone or "",
        "email":  event.person_ref.email or "",
        "website": "",
        "city":   "",
        "job_title": "",
        "verified_fact":  f"{event.channel}:{event.response_type}",
        "trigger_event":  event.subtype or event.response_type,
    }
    try:
        from services.twenty_schema import (
            upsert_person_with_score,
            create_signal_record,
        )
        person_result = upsert_person_with_score(
            business_key=business_key,
            person=p,
            score_snapshot=None,  # score arrives via S3 (item 3); this is S1 raw ingest
            source_agent="intent_inbound",
        )
    except Exception as e:
        logger.exception("[intent-inbound] Twenty person upsert raised: %s", e)
        return {"status": "twenty_error", "reason": f"{type(e).__name__}: {e}"}

    person_id = person_result.get("contact_id") or ""
    signal_result: Dict[str, Any] = {"status": "not_attempted"}
    if person_id:
        try:
            signal_result = create_signal_record(
                business_key=business_key,
                signal_row={
                    "person_id":      person_id,
                    "brand":          event.brand,
                    "channel":        event.channel,
                    "response_type":  event.response_type,
                    "subtype":        event.subtype or "",
                    "source_name":    event.raw_body.get("source_name", ""),
                    "occurred_at":    event.timestamp.isoformat(),
                    "idempotency_key": idem,
                },
            )
        except Exception as e:
            logger.exception("[intent-inbound] Signal write raised: %s", e)
            signal_result = {"status": "error", "reason": f"{type(e).__name__}: {e}"}

    return {
        "status":        person_result.get("status") or "unknown",
        "twenty_id":     person_id,
        "score_stamped": bool(person_result.get("score_stamped")),
        "business_key":  business_key,
        "signal":        signal_result,
    }


# ---------------------------------------------------------------------------
# Top-level entry (called by the FastAPI endpoint in app.py)
# ---------------------------------------------------------------------------


def handle_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Parse + validate + persist + write. Called by the endpoint after the
    path-secret check. Never raises on Twenty failures (persists the event
    with the twenty_status so we can retry from the audit table)."""
    try:
        event = IntentInboundEvent(**payload)
    except Exception as e:
        return {
            "ok": False,
            "status": "bad_payload",
            "reason": str(e),
        }
    idem = _derive_idempotency_key(event)
    twenty_result = _write_to_twenty(event, idem)
    audit_result = _persist_audit(event, idem, twenty_result)
    logger.info(
        "[intent-inbound] brand=%s channel=%s type=%s deduped=%s twenty=%s",
        event.brand, event.channel, event.response_type,
        audit_result.get("deduped"), twenty_result.get("status"),
    )
    return {
        "ok": True,
        "brand": event.brand,
        "channel": event.channel,
        "response_type": event.response_type,
        "idempotency_key": idem,
        "audit": audit_result,
        "twenty": twenty_result,
    }


# ---------------------------------------------------------------------------
# Shim helpers -- for the existing Instantly endpoint to forward its payloads
# into this pipeline without changing its GHL-side behavior.
# ---------------------------------------------------------------------------


def instantly_reply_to_event(brand: str, instantly_payload: Dict[str, Any]) -> Optional[IntentInboundEvent]:
    """Adapt an Instantly reply payload into an IntentInboundEvent. Returns
    None if the payload is not a reply event (silently no-op)."""
    event_type = (instantly_payload.get("event_type") or instantly_payload.get("event") or "").lower()
    if "reply" not in event_type:
        return None
    email = (instantly_payload.get("lead_email") or instantly_payload.get("email") or "").strip().lower()
    if not email:
        return None
    ts_raw = instantly_payload.get("timestamp") or instantly_payload.get("received_at")
    if ts_raw:
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except ValueError:
            ts = datetime.now(timezone.utc)
    else:
        ts = datetime.now(timezone.utc)
    return IntentInboundEvent(
        brand=brand,
        person_ref=PersonRef(email=email),
        channel="cold_email",
        response_type="inbound_reply",
        raw_body=instantly_payload,
        timestamp=ts,
    )


def path_secret_ok(sent: str) -> bool:
    """Constant-time compare against INTENT_INBOUND_WEBHOOK_PATH_SECRET."""
    import hmac
    expected = (os.getenv("INTENT_INBOUND_WEBHOOK_PATH_SECRET") or "").strip()
    if not expected:
        # Fail closed: if the env is unset in prod, treat every request as
        # unauthorized rather than silently opening the door.
        return False
    return hmac.compare_digest(sent, expected)


__all__ = [
    "ChannelName",
    "IntentInboundEvent",
    "PersonRef",
    "ResponseType",
    "handle_event",
    "instantly_reply_to_event",
    "path_secret_ok",
]
