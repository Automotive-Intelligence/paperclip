"""tools/brand_send.py — the SEND-AS-BRAND rail (gated OFF by default).

Gives a seat (the Sales Desk AE) the ability to send a 1:1 email AS a brand
identity, with an attachment, behind a HARD authorization gate. This is the
capability. It does NOT flip it on. The instant Michael draws the send-authority
boundary (by adding a mailbox to SEND_AUTHORIZED_MAILBOXES), it activates — not
before.

Why this exists
---------------
Today the only outbound is the Gmail connector's create_draft (salesdroid only,
can't actually send). The postal suite (classifier / inbox / router / writers /
escalation) is INBOX-ONLY — no send path. Loops can't do 1:1-with-attachment and
isn't the right tool for a personal, attached sample. The concrete first target
is being able to send Brian Leija's overdue sample AS michael@worshipdigital.co
with the sample CSV attached — but only once Michael authorizes that identity.

The three guarantees
--------------------
1. HARD AUTHORIZATION GATE (default OFF).
   The set of identities allowed to actually fire is read from the env var
   SEND_AUTHORIZED_MAILBOXES (comma-separated addresses). It is EMPTY by
   default. If the from-identity is not explicitly listed, the tool DRAFTS +
   logs + returns outcome="held" and NEVER calls the transport. This is the code
   embodiment of Michael's send-authority boundary. No identity is hardcoded as
   authorized anywhere in this file.

2. AUDIT ENVELOPE.
   Every intended send — fired, held, or degraded to draft — is written to the
   durable brand_send_audit store (who/seat, from-identity, to, subject,
   attachment, timestamp, authorized y/n, outcome).

3. PLUGGABLE TRANSPORT, degrade-not-crash.
   The mechanism is the Gmail / Workspace API send path (users.messages.send).
   The transport is abstracted behind a small protocol so the credential is
   pluggable and mockable. With NO credential configured AND the gate OFF,
   everything degrades to draft-and-log — it never raises.

CREDENTIAL PREREQUISITE (owner / infra — NOT fabricated here)
-------------------------------------------------------------
To actually fire a send AS michael@worshipdigital.co, the worshipdigital.co
Google Workspace tenant must grant one of:

  (A) Workspace OAuth with the gmail.send scope for that mailbox. The existing
      Postal OAuth client (POSTAL_GOOGLE_CLIENT_ID/SECRET) requests only
      gmail.readonly + gmail.modify + gmail.labels (see services/postal_oauth.py
      SCOPES) — it deliberately does NOT include gmail.send. Firing sends
      requires re-consenting the "wd" mailbox with an OAuth scope set that adds
      https://www.googleapis.com/auth/gmail.send. That is a scope change +
      re-auth on the worshipdigital.co tenant, performed by the owner.

  (B) A Google Cloud service account with DOMAIN-WIDE DELEGATION authorized for
      the worshipdigital.co Workspace, delegated to impersonate
      michael@worshipdigital.co, with the gmail.send scope granted in the
      Admin console. The service-account JSON key is then supplied out-of-band
      (Doppler paperclip/prd, e.g. WD_WORKSPACE_SA_JSON) and wired into a
      GmailSendTransport. No key is read or hardcoded in this module.

Until (A) or (B) exists, get_default_transport() returns None and every send
degrades to draft-and-log. This tool does not fabricate, hardcode, or invent
any credential.

Env
---
    SEND_AUTHORIZED_MAILBOXES   comma-separated from-identities allowed to FIRE.
                                EMPTY by default (nothing can send). Michael's
                                boundary; do not populate it in code or config.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, Optional, Protocol

from services import brand_send_audit

logger = logging.getLogger(__name__)

# Brand-identity label -> the address a send would go out AS. This is the ADDRESS
# BOOK only — being listed here grants NOTHING. Firing still requires the address
# to appear in SEND_AUTHORIZED_MAILBOXES (the gate) AND a working transport.
BRAND_IDENTITIES: dict[str, str] = {
    "wd": "michael@worshipdigital.co",
    "avi": "michael@automotiveintelligence.io",
}

_AUTH_ENV = "SEND_AUTHORIZED_MAILBOXES"


class BrandSendError(RuntimeError):
    """A brand-send call could not even be attempted (bad input, etc.)."""


# ---------------------------------------------------------------------------
# Result envelope
# ---------------------------------------------------------------------------

@dataclass
class SendResult:
    """What a brand_send attempt did. Always returned; never a raised send."""
    outcome: str                       # sent | held | drafted | error
    sent: bool                         # True only when transport accepted it
    authorized: bool                   # was the from-identity on the allowlist?
    from_identity: str
    to_addr: str
    subject: str
    attachment: Optional[str]
    seat: str
    message: str = ""                  # human-readable status
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "sent": self.sent,
            "authorized": self.authorized,
            "from_identity": self.from_identity,
            "to": self.to_addr,
            "subject": self.subject,
            "attachment": self.attachment,
            "seat": self.seat,
            "message": self.message,
            "detail": self.detail,
        }


# ---------------------------------------------------------------------------
# Authorization gate
# ---------------------------------------------------------------------------

def authorized_mailboxes() -> set[str]:
    """Parse SEND_AUTHORIZED_MAILBOXES into a set of lowercased addresses.

    EMPTY by default. This is the ONLY thing that can turn a draft into a real
    send. It is read from the environment on every call so Michael drawing the
    boundary takes effect without a redeploy of this module.
    """
    raw = (os.environ.get(_AUTH_ENV) or "").strip()
    if not raw:
        return set()
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def is_authorized(from_identity: str) -> bool:
    """True iff this from-identity is explicitly on the allowlist."""
    return (from_identity or "").strip().lower() in authorized_mailboxes()


# ---------------------------------------------------------------------------
# Transport abstraction (pluggable + mockable)
# ---------------------------------------------------------------------------

class SendTransport(Protocol):
    """A pluggable send mechanism. Implementations own the credential."""

    def send_raw(self, from_identity: str, raw_rfc822_b64: str) -> dict[str, Any]:
        """Actually transmit the message. Return a transport-native receipt."""
        ...


class GmailSendTransport:
    """Gmail / Workspace API send path (users.messages.send).

    The credential is supplied by the caller as a ready-to-use google-auth
    Credentials object (with the gmail.send scope) — this class does NOT decide
    where that credential comes from. In production it is built from either
    Workspace OAuth (gmail.send re-consent) or a domain-wide-delegation service
    account impersonating the brand mailbox (see module docstring). Neither is
    hardcoded here; get_default_transport() returns None until infra wires one.
    """

    def __init__(self, credentials: Any):
        self._credentials = credentials

    def send_raw(self, from_identity: str, raw_rfc822_b64: str) -> dict[str, Any]:
        from googleapiclient.discovery import build  # lazy: dep only in prod/CI

        service = build("gmail", "v1", credentials=self._credentials, cache_discovery=False)
        # userId="me" resolves to the authorized/impersonated mailbox; the raw
        # message already carries the From: header for from_identity.
        return service.users().messages().send(
            userId="me",
            body={"raw": raw_rfc822_b64},
        ).execute()


def get_default_transport(from_identity: str) -> Optional[SendTransport]:
    """Return a live transport for this identity, or None if none is wired.

    Returns None today for every identity: the gmail.send credential for the
    worshipdigital.co tenant is an owner/infra prerequisite (see module
    docstring) and is intentionally NOT fabricated or hardcoded here. When infra
    wires the credential, this factory is where it plugs in. Returning None makes
    the whole rail degrade to draft-and-log — it never crashes.
    """
    return None


# ---------------------------------------------------------------------------
# Message building (the same RFC822 whether we fire or just draft)
# ---------------------------------------------------------------------------

def build_message(
    *,
    from_identity: str,
    to_addr: str,
    subject: str,
    body: str,
    attachment_path: Optional[str] = None,
) -> EmailMessage:
    """Build a MIME message with optional file attachment.

    Raises BrandSendError if an attachment is named but missing on disk — we
    never silently drop an attachment the caller expected to send.
    """
    msg = EmailMessage()
    msg["From"] = from_identity
    msg["To"] = to_addr
    msg["Subject"] = subject or ""
    msg.set_content(body or "")

    if attachment_path:
        if not os.path.isfile(attachment_path):
            raise BrandSendError(f"attachment not found: {attachment_path}")
        ctype, encoding = mimetypes.guess_type(attachment_path)
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        with open(attachment_path, "rb") as fh:
            data = fh.read()
        msg.add_attachment(
            data,
            maintype=maintype,
            subtype=subtype,
            filename=os.path.basename(attachment_path),
        )
    return msg


def _raw_b64(msg: EmailMessage) -> str:
    """URL-safe base64 of the raw RFC822, as Gmail's messages.send expects."""
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


# ---------------------------------------------------------------------------
# The guarded send
# ---------------------------------------------------------------------------

def send_as_brand(
    *,
    to: str,
    subject: str,
    body: str,
    from_identity: str,
    attachment_path: Optional[str] = None,
    seat: str = "sales_desk_ae",
    transport: Optional[SendTransport] = None,
) -> SendResult:
    """Send a 1:1 email AS a brand identity — behind the hard authorization gate.

    `from_identity` may be a brand label ("wd") or a full address
    ("michael@worshipdigital.co"); labels resolve via BRAND_IDENTITIES.

    Control flow (in order):
      1. Resolve + validate the from-identity and recipient.
      2. Build the MIME message (incl. attachment) — this is done for EVERY
         path, so a "held" or "drafted" message is a real, ready-to-send draft.
      3. GATE: if the identity is NOT on SEND_AUTHORIZED_MAILBOXES, record a
         "held" envelope and RETURN — the transport is never touched.
      4. If authorized but no transport/credential, record "drafted" and RETURN
         (degrade, do not crash).
      5. If authorized AND a transport exists, fire; record "sent" or "error".

    Always returns a SendResult and always writes exactly one audit envelope.
    """
    to_addr = (to or "").strip()
    if not to_addr:
        raise BrandSendError("`to` is required")

    resolved_from = BRAND_IDENTITIES.get(
        (from_identity or "").strip().lower(),
        (from_identity or "").strip(),
    )
    if not resolved_from or "@" not in resolved_from:
        raise BrandSendError(
            f"could not resolve from-identity '{from_identity}'. "
            f"use a full address or a known label {sorted(BRAND_IDENTITIES)}"
        )

    # Build the message up front (validates attachment too). If this raises, no
    # envelope is written because nothing was ever intended-to-send coherently.
    msg = build_message(
        from_identity=resolved_from,
        to_addr=to_addr,
        subject=subject,
        body=body,
        attachment_path=attachment_path,
    )

    authorized = is_authorized(resolved_from)

    def _finish(outcome: str, sent: bool, message: str, extra: Optional[dict] = None) -> SendResult:
        detail = {"has_attachment": bool(attachment_path)}
        if extra:
            detail.update(extra)
        brand_send_audit.record(
            seat=seat,
            from_identity=resolved_from,
            to_addr=to_addr,
            subject=subject or "",
            attachment=os.path.basename(attachment_path) if attachment_path else None,
            authorized=authorized,
            outcome=outcome,
            detail=detail,
        )
        return SendResult(
            outcome=outcome,
            sent=sent,
            authorized=authorized,
            from_identity=resolved_from,
            to_addr=to_addr,
            subject=subject or "",
            attachment=os.path.basename(attachment_path) if attachment_path else None,
            seat=seat,
            message=message,
            detail=detail,
        )

    # --- STEP 3: the hard gate. Not authorized => held, transport never touched.
    if not authorized:
        return _finish(
            "held",
            sent=False,
            message=(
                f"held: not authorized. '{resolved_from}' is not in "
                f"{_AUTH_ENV}; drafted + logged, not sent. "
                "Add it to the allowlist to authorize sending."
            ),
        )

    # --- STEP 4: authorized, but is there a credential/transport to fire with?
    active_transport = transport if transport is not None else get_default_transport(resolved_from)
    if active_transport is None:
        return _finish(
            "drafted",
            sent=False,
            message=(
                f"authorized but no send credential wired for '{resolved_from}' — "
                "degraded to draft-and-log. See tools/brand_send.py docstring for "
                "the Workspace OAuth (gmail.send) or domain-wide-delegation "
                "service-account prerequisite."
            ),
            extra={"reason": "no_transport"},
        )

    # --- STEP 5: authorized AND transport present => actually fire.
    try:
        receipt = active_transport.send_raw(resolved_from, _raw_b64(msg))
    except Exception as e:  # noqa: BLE001 — record then surface a clean result
        logger.exception("brand_send transport error")
        return _finish(
            "error",
            sent=False,
            message=f"transport error: {e}",
            extra={"error": str(e)[:300]},
        )

    return _finish(
        "sent",
        sent=True,
        message=f"sent as {resolved_from} to {to_addr}",
        extra={"receipt_id": (receipt or {}).get("id")},
    )


# ---------------------------------------------------------------------------
# MCP registration surface (matches tools/postal_inbox_tools.py convention)
# ---------------------------------------------------------------------------

def _tool_send_as_brand(
    to: str,
    subject: str,
    body: str,
    from_identity: str,
    attachment_path: Optional[str] = None,
    seat: str = "sales_desk_ae",
) -> dict[str, Any]:
    """MCP handler: returns the SendResult as a plain dict."""
    return send_as_brand(
        to=to,
        subject=subject,
        body=body,
        from_identity=from_identity,
        attachment_path=attachment_path,
        seat=seat,
    ).to_dict()


BRAND_SEND_TOOLS: list[dict[str, Any]] = [
    {
        "name": "send_as_brand",
        "description": (
            "Send a 1:1 email AS a brand identity with an optional file "
            "attachment, behind a hard authorization gate. If the from-identity "
            "is not on SEND_AUTHORIZED_MAILBOXES, the message is drafted + logged "
            "and HELD (never sent). Every attempt is written to the durable audit "
            "envelope. Returns {outcome, sent, authorized, ...}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address."},
                "subject": {"type": "string", "description": "Email subject."},
                "body": {"type": "string", "description": "Plain-text body."},
                "from_identity": {
                    "type": "string",
                    "description": (
                        "Brand label ('wd') or full address to send AS. "
                        f"Known labels: {sorted(BRAND_IDENTITIES)}."
                    ),
                },
                "attachment_path": {
                    "type": "string",
                    "description": "Absolute path to a file to attach (optional).",
                },
                "seat": {
                    "type": "string",
                    "description": "Requesting seat, for the audit envelope.",
                    "default": "sales_desk_ae",
                },
            },
            "required": ["to", "subject", "body", "from_identity"],
        },
        "handler": _tool_send_as_brand,
    },
]

BRAND_SEND_HANDLERS = {t["name"]: t["handler"] for t in BRAND_SEND_TOOLS}


def dispatch(name: str, **kwargs: Any) -> dict[str, Any]:
    """Call a brand-send tool by its registered MCP name."""
    handler = BRAND_SEND_HANDLERS.get(name)
    if handler is None:
        raise BrandSendError(f"unknown brand-send tool '{name}'.")
    return handler(**kwargs)


__all__ = [
    "BRAND_IDENTITIES",
    "BrandSendError",
    "SendResult",
    "SendTransport",
    "GmailSendTransport",
    "get_default_transport",
    "authorized_mailboxes",
    "is_authorized",
    "build_message",
    "send_as_brand",
    "BRAND_SEND_TOOLS",
    "BRAND_SEND_HANDLERS",
    "dispatch",
]
