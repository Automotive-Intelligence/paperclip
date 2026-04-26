"""services/approval_digest.py — Weekly batched approval digest emails.

Sits on top of services/approval_queue.py. Pulls all pending_approval artifacts
for a given business_key, renders one HTML email containing every item with
inline previews + 3 action buttons (Approve / Edit / Reject), and emails the
client. Buttons are HMAC-signed URLs so clicks can't be forged.

Currently used for the Paper and Purpose campaign (Miriam Rubio). Extends
trivially to other tenants by setting per-business env vars.

Configuration (Railway):
  DIGEST_TOKEN_SECRET                       HMAC secret. REQUIRED.
  DIGEST_BASE_URL                           e.g. https://paperclip-production-ba14.up.railway.app
  DIGEST_RECIPIENT_<BUSINESSKEY>            e.g. DIGEST_RECIPIENT_PAPER_AND_PURPOSE=miriam@...
  DIGEST_REPLY_TO_<BUSINESSKEY>             optional; defaults to MAIL_FROM_<BUSINESSKEY>
  DIGEST_TOKEN_TTL_DAYS                     optional; default 14

Action token shape:
  Base64URL(payload) + "." + Base64URL(HMAC-SHA256(secret, payload))
where payload = "<artifact_id>|<action>|<expires_unix_ts>"

The token is bound to (artifact_id, action) — a token issued for "approve"
on artifact X cannot be replayed against "reject" on artifact X or against
artifact Y. Artifact status checks make actions naturally idempotent
(once approved, can't be approved again).
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import html
import logging
import os
import re
from typing import Any

from services.approval_queue import list_artifacts

logger = logging.getLogger(__name__)

DEFAULT_TTL_DAYS = 14
ALLOWED_ACTIONS = ("approve", "reject", "edit")


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _business_env_suffix(business_key: str) -> str:
    """Mirror tools/outbound_email._business_env_suffix so digest config keys
    stay consistent with email config keys."""
    return re.sub(r"[^a-z0-9]", "", (business_key or "").strip().lower()).upper()


def _get_secret() -> str | None:
    return (os.environ.get("DIGEST_TOKEN_SECRET") or "").strip() or None


def _get_base_url() -> str:
    return (os.environ.get("DIGEST_BASE_URL") or "").strip().rstrip("/")


def _recipient_for_business(business_key: str) -> str | None:
    suffix = _business_env_suffix(business_key)
    if not suffix:
        return None
    val = os.environ.get(f"DIGEST_RECIPIENT_{suffix}", "").strip()
    return val or None


def _ttl_days() -> int:
    raw = os.environ.get("DIGEST_TOKEN_TTL_DAYS", "").strip()
    try:
        return max(1, int(raw)) if raw else DEFAULT_TTL_DAYS
    except ValueError:
        return DEFAULT_TTL_DAYS


# ---------------------------------------------------------------------------
# HMAC token helpers
# ---------------------------------------------------------------------------

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    s_pad = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s_pad.encode("ascii"))


def sign_action_token(artifact_id: str, action: str, ttl_days: int | None = None) -> str:
    """Mint a token authorizing exactly one (artifact_id, action) tuple."""
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"action must be one of {ALLOWED_ACTIONS}; got {action!r}")
    secret = _get_secret()
    if not secret:
        raise RuntimeError("DIGEST_TOKEN_SECRET is not set")

    expires = int(
        (
            datetime.datetime.utcnow()
            + datetime.timedelta(days=ttl_days or _ttl_days())
        ).timestamp()
    )
    payload = f"{artifact_id}|{action}|{expires}".encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return f"{_b64url(payload)}.{_b64url(sig)}"


def verify_action_token(token: str, expected_artifact_id: str, expected_action: str) -> tuple[bool, str]:
    """Return (ok, reason). reason is a short string describing why if not ok."""
    secret = _get_secret()
    if not secret:
        return False, "server misconfigured: DIGEST_TOKEN_SECRET not set"
    if not token or "." not in token:
        return False, "malformed token"

    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _b64url_decode(payload_b64)
        provided_sig = _b64url_decode(sig_b64)
    except Exception:
        return False, "malformed token"

    expected_sig = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(expected_sig, provided_sig):
        return False, "bad signature"

    try:
        artifact_id, action, expires_str = payload.decode("utf-8").split("|")
        expires = int(expires_str)
    except Exception:
        return False, "malformed payload"

    if artifact_id != expected_artifact_id:
        return False, "token bound to a different artifact"
    if action != expected_action:
        return False, "token bound to a different action"
    if datetime.datetime.utcnow().timestamp() > expires:
        return False, "token expired"

    return True, "ok"


# ---------------------------------------------------------------------------
# Digest building / rendering
# ---------------------------------------------------------------------------

def _format_scheduled_for(meta: dict[str, Any]) -> str:
    raw = meta.get("scheduled_for") or meta.get("publish_at") or ""
    return str(raw) if raw else "unscheduled"


def _platform_label(meta: dict[str, Any], channel_candidates: list[str]) -> str:
    plat = meta.get("platform") or meta.get("channel")
    if plat:
        return str(plat)
    if channel_candidates:
        return ", ".join(channel_candidates)
    return "unspecified"


def _action_url(base: str, artifact_id: str, action: str) -> str:
    token = sign_action_token(artifact_id, action)
    return f"{base}/m/{action}/{artifact_id}?t={token}"


def _render_item_html(item: dict[str, Any], base_url: str, idx: int) -> str:
    """Render one queued artifact as an HTML block with inline preview + 3 buttons."""
    aid = item["artifact_id"]
    subject = html.escape(item.get("subject") or "(untitled)")
    content = html.escape(item.get("content") or "")
    meta = item.get("metadata") or {}
    image_url = meta.get("image_url") or meta.get("preview_url")
    platform = html.escape(_platform_label(meta, item.get("channel_candidates") or []))
    scheduled = html.escape(_format_scheduled_for(meta))
    intent = html.escape(item.get("intent") or "")
    artifact_type = html.escape(item.get("artifact_type") or "")

    image_block = (
        f'<div style="margin:12px 0"><img src="{html.escape(image_url)}" '
        f'alt="preview" style="max-width:480px;width:100%;border-radius:8px;border:1px solid #eee"></div>'
        if image_url
        else ""
    )

    approve_url = _action_url(base_url, aid, "approve")
    edit_url = _action_url(base_url, aid, "edit")
    reject_url = _action_url(base_url, aid, "reject")

    button_style_base = (
        "display:inline-block;padding:10px 18px;margin:4px 6px 4px 0;"
        "border-radius:6px;font-weight:600;font-size:14px;"
        "text-decoration:none;font-family:Inter,system-ui,Arial,sans-serif;"
    )
    btn_approve = f'background:#16a34a;color:#fff;{button_style_base}'
    btn_edit = f'background:#fff;color:#374151;border:1px solid #d1d5db;{button_style_base}'
    btn_reject = f'background:#fff;color:#b91c1c;border:1px solid #fecaca;{button_style_base}'

    return f"""
    <div style="border:1px solid #e5e7eb;border-radius:10px;padding:18px;margin:14px 0;background:#fff">
      <div style="font-size:13px;color:#6b7280;margin-bottom:6px">
        Post #{idx} &middot; {artifact_type} &middot; {intent}
      </div>
      <div style="font-size:18px;font-weight:600;color:#111827;margin-bottom:10px">{subject}</div>
      <div style="font-size:13px;color:#374151;margin-bottom:6px">
        <strong>Platform:</strong> {platform} &nbsp;·&nbsp;
        <strong>Scheduled:</strong> {scheduled}
      </div>
      {image_block}
      <pre style="white-space:pre-wrap;font-family:Inter,system-ui,Arial,sans-serif;font-size:14px;color:#1f2937;margin:12px 0;padding:12px;background:#f9fafb;border-radius:6px;border:1px solid #f3f4f6">{content}</pre>
      <div style="margin-top:14px">
        <a href="{approve_url}" style="{btn_approve}">✓ Approve</a>
        <a href="{edit_url}" style="{btn_edit}">✎ Edit</a>
        <a href="{reject_url}" style="{btn_reject}">✗ Reject</a>
      </div>
    </div>
    """


def render_digest_html(items: list[dict[str, Any]], business_key: str, base_url: str) -> str:
    """Render the full digest email body."""
    today = datetime.date.today().strftime("%a, %b %d %Y")
    n = len(items)
    item_blocks = "".join(_render_item_html(it, base_url, i + 1) for i, it in enumerate(items))

    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:24px;background:#f3f4f6;font-family:Inter,system-ui,Arial,sans-serif;color:#111827">
  <div style="max-width:640px;margin:0 auto;background:#f3f4f6">
    <div style="background:#fff;border-radius:12px;padding:24px;margin-bottom:16px">
      <div style="font-size:13px;color:#6b7280;text-transform:uppercase;letter-spacing:0.06em">{html.escape(business_key)} &middot; weekly approval digest</div>
      <h2 style="font-size:22px;color:#111827;margin:8px 0 6px 0">{n} {'post' if n == 1 else 'posts'} ready for your review</h2>
      <div style="font-size:14px;color:#374151;line-height:1.5">
        Tap one button on each post — <strong>Approve</strong>, <strong>Edit</strong>, or <strong>Reject</strong>.
        Approved posts publish on schedule. Rejected posts go back to the team for a redo.
        Reply to this email anytime to talk to a human.
      </div>
      <div style="font-size:12px;color:#9ca3af;margin-top:8px">{today}</div>
    </div>
    {item_blocks}
    <div style="font-size:12px;color:#9ca3af;text-align:center;padding:16px">
      Calling Digital &middot; You're receiving this because you're the active client owner for {html.escape(business_key)}.
    </div>
  </div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Send orchestrator
# ---------------------------------------------------------------------------

def build_and_send_digest(business_key: str, *, dry_run: bool = False) -> dict[str, Any]:
    """Pull pending artifacts for business_key, render digest, send email.

    Returns a result dict with keys:
      ok           bool
      reason       str (set when ok=False or skipped)
      business_key str
      pending      int (count of pending artifacts)
      to           str (recipient if sent / would-have-been-sent)
      sent         bool
    """
    result: dict[str, Any] = {
        "ok": False,
        "business_key": business_key,
        "pending": 0,
        "to": None,
        "sent": False,
        "reason": "",
    }

    if not _get_secret():
        result["reason"] = "DIGEST_TOKEN_SECRET not set"
        return result
    base_url = _get_base_url()
    if not base_url:
        result["reason"] = "DIGEST_BASE_URL not set"
        return result
    recipient = _recipient_for_business(business_key)
    if not recipient:
        result["reason"] = f"DIGEST_RECIPIENT_{_business_env_suffix(business_key)} not set"
        return result

    pending = list_artifacts(business_key=business_key, status="pending_approval", limit=200)
    result["pending"] = len(pending)
    result["to"] = recipient

    if not pending:
        result["ok"] = True
        result["reason"] = "no pending artifacts; nothing to send"
        return result

    body_html = render_digest_html(pending, business_key, base_url)
    subject = f"✓ {len(pending)} {'post' if len(pending) == 1 else 'posts'} ready for your review — {business_key}"

    if dry_run:
        result["ok"] = True
        result["reason"] = "dry_run"
        return result

    # Resend supports `html` directly; outbound_email's send_unified_email
    # currently only sends `text`. We call Resend directly here so the email
    # renders with inline buttons + image previews properly.
    from tools.outbound_email import _resend_api_key, _mail_from_for_business
    api_key = _resend_api_key(business_key)
    from_addr = _mail_from_for_business(business_key)
    if not (api_key and from_addr):
        result["reason"] = (
            f"Resend not configured for business_key={business_key} "
            f"(need RESEND_API_KEY[_<SUFFIX>] + MAIL_FROM[_<SUFFIX>])"
        )
        return result

    import requests
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": from_addr,
                "to": [recipient],
                "subject": subject,
                "html": body_html,
            },
            timeout=20,
        )
    except Exception as e:
        result["reason"] = f"Resend request failed: {type(e).__name__}: {e}"
        return result

    if resp.status_code in (200, 201):
        result["ok"] = True
        result["sent"] = True
        result["reason"] = "sent"
        logger.info(
            "[digest] sent business_key=%s recipient=%s items=%d",
            business_key, recipient, len(pending),
        )
    else:
        result["reason"] = f"Resend error HTTP {resp.status_code}: {resp.text[:200]}"
        logger.error("[digest] %s", result["reason"])

    return result


def known_business_keys_with_pending() -> list[str]:
    """Distinct business_keys with at least one pending_approval artifact.
    Used by the weekly cron to send a digest per active tenant.

    Phase 1 implementation: cheap query — pull all pending and unique-ify.
    For scale, swap to a SELECT DISTINCT query later.
    """
    rows = list_artifacts(status="pending_approval", limit=200)
    seen: list[str] = []
    out: list[str] = []
    for r in rows:
        bk = r.get("business_key") or ""
        if bk and bk not in seen:
            seen.append(bk)
            out.append(bk)
    return out


def status_summary() -> dict[str, Any]:
    """Lightweight observability for /admin endpoints."""
    return {
        "secret_set": bool(_get_secret()),
        "base_url": _get_base_url() or None,
        "ttl_days": _ttl_days(),
        "configured_recipients": {
            re.sub(r"^DIGEST_RECIPIENT_", "", k): bool(v.strip())
            for k, v in os.environ.items()
            if k.startswith("DIGEST_RECIPIENT_") and v.strip()
        },
    }
