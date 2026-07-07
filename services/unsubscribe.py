"""services/unsubscribe.py -- RFC 8058 one-click unsubscribe token + headers.

COMPLIANCE-CRITICAL. Google/Yahoo bulk-sender rules (Feb 2024) require bulk
senders to support one-click unsubscribe via the `List-Unsubscribe` +
`List-Unsubscribe-Post` headers (RFC 8058) AND a working unsubscribe mechanism
that honors the request within 2 days.

This module is the token + URL + header layer. It is transport-agnostic:

  - `make_token(email, brand)` -> opaque, signed, path-safe token
  - `parse_token(token)` -> (email, brand) or None (signature-verified)
  - `unsubscribe_url(email, brand, base_url=None)` -> the click/POST target
  - `list_unsubscribe_headers(email, brand, base_url=None)` -> the two RFC 8058
    header values a sending layer must emit

The token is HMAC-SHA256 signed with UNSUBSCRIBE_SIGNING_SECRET so the
unsubscribe endpoint can trust that a given token maps to a specific
(email, brand) pair without a database lookup, and so tokens cannot be forged
to unsubscribe arbitrary addresses.

FAIL-CLOSED design:
  - No signing secret -> make_token / unsubscribe_url RAISE. A caller that
    cannot mint a verifiable unsubscribe link must NOT proceed to enroll/send
    (a send with a non-working unsubscribe link is itself non-compliant).
  - parse_token returns None on any tampering / bad signature / missing secret,
    so the endpoint refuses to act on an untrusted token rather than
    suppressing (or falsely confirming) the wrong address.

The token is a single URL-PATH segment with NO query string and NO '&', which
matters because the Instantly HTML renderer nukes any body containing '&'
(see intent_workflow_runner._instantly_html). The literal merge tag
`{{unsubscribe_url}}` carries no '&' either; Instantly substitutes the real
per-recipient URL at send time.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Dict, Optional, Tuple

_SECRET_ENV = "UNSUBSCRIBE_SIGNING_SECRET"
_BASE_URL_ENV = "PUBLIC_UNSUB_BASE_URL"  # e.g. https://paperclip.up.railway.app
_TOKEN_VERSION = "v1"


class UnsubscribeConfigError(RuntimeError):
    """Raised when the signing secret (or base URL) required to mint a
    verifiable one-click unsubscribe link is not configured. FAIL-CLOSED:
    a caller that hits this must NOT enroll/send."""


def _secret() -> bytes:
    raw = (os.getenv(_SECRET_ENV) or "").strip()
    if not raw:
        raise UnsubscribeConfigError(
            f"{_SECRET_ENV} not set; cannot mint/verify one-click unsubscribe "
            f"tokens. Refusing to proceed (fail-closed)."
        )
    return raw.encode("utf-8")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sign(payload_b64: str) -> str:
    msg = f"{_TOKEN_VERSION}.{payload_b64}".encode("ascii")
    return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()


def make_token(email: str, brand: str) -> str:
    """Return a signed, URL-path-safe token encoding (email, brand).

    Format: ``v1.<b64url(json)>.<hex-hmac>``  — no '&', no '?', no padding.
    Raises UnsubscribeConfigError if the signing secret is unset (fail-closed).
    """
    email = (email or "").strip().lower()
    brand = (brand or "").strip().lower()
    if not email or not brand:
        raise ValueError("make_token requires non-empty email and brand")
    payload = json.dumps({"e": email, "b": brand}, separators=(",", ":"), sort_keys=True)
    payload_b64 = _b64url_encode(payload.encode("utf-8"))
    sig = _sign(payload_b64)
    return f"{_TOKEN_VERSION}.{payload_b64}.{sig}"


def parse_token(token: str) -> Optional[Tuple[str, str]]:
    """Verify a token's signature and return (email, brand), or None.

    Returns None (never raises) on ANY problem — missing secret, wrong shape,
    bad signature, undecodable payload — so the endpoint fails closed by
    refusing to act on an untrusted token.
    """
    if not token or token.count(".") != 2:
        return None
    version, payload_b64, sig = token.split(".", 2)
    if version != _TOKEN_VERSION:
        return None
    try:
        expected = _sign(payload_b64)
    except UnsubscribeConfigError:
        return None
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        data = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception:
        return None
    email = (data.get("e") or "").strip().lower()
    brand = (data.get("b") or "").strip().lower()
    if not email or not brand:
        return None
    return email, brand


def _resolve_base_url(base_url: Optional[str]) -> str:
    base = (base_url or os.getenv(_BASE_URL_ENV) or "").strip().rstrip("/")
    if not base:
        raise UnsubscribeConfigError(
            f"{_BASE_URL_ENV} not set and no base_url passed; cannot build a "
            f"reachable unsubscribe URL. Refusing to proceed (fail-closed)."
        )
    return base


def unsubscribe_url(email: str, brand: str, base_url: Optional[str] = None) -> str:
    """Return the fully-qualified one-click unsubscribe URL for this recipient.

    ``<base>/u/<token>`` — a single path segment, no query string, so it
    survives the Instantly HTML sanitizer. Raises UnsubscribeConfigError
    (fail-closed) if the signing secret or base URL is unavailable.
    """
    base = _resolve_base_url(base_url)
    return f"{base}/u/{make_token(email, brand)}"


def list_unsubscribe_headers(
    email: str, brand: str, base_url: Optional[str] = None
) -> Dict[str, str]:
    """Return the two RFC 8058 headers a sending layer MUST emit for one-click.

        List-Unsubscribe: <https://.../u/TOKEN>
        List-Unsubscribe-Post: List-Unsubscribe=One-Click

    Raises UnsubscribeConfigError (fail-closed) if the link can't be minted.
    """
    url = unsubscribe_url(email, brand, base_url=base_url)
    return {
        "List-Unsubscribe": f"<{url}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


def unsubscribe_ready() -> bool:
    """True iff both the signing secret and a base URL are configured, i.e. we
    can mint verifiable one-click unsubscribe links. Used as a pre-send gate."""
    try:
        _secret()
        _resolve_base_url(None)
        return True
    except UnsubscribeConfigError:
        return False


__all__ = [
    "UnsubscribeConfigError",
    "make_token",
    "parse_token",
    "unsubscribe_url",
    "list_unsubscribe_headers",
    "unsubscribe_ready",
]
