"""Postal Agent escalation — batched "needs a human" alert to Michael.

After each sweep the agent collects the inbounds it judged genuinely
human-actionable (real prospects, real money problems, genuine account
security — see postal_classifier.should_escalate) and this module sends ONE
batched email + ONE SMS digest. It is never one-alert-per-message, so a busy
sweep cannot text-storm you; dedupe is inherent because each message is
processed once (postal_processed).

Safety gate
-----------
Real alerts are gated behind POSTAL_ESCALATE_ENABLED. When unset/false this is
a no-op that logs what it WOULD send ("shadow"), so escalation can be watched
for a day before any real texts start — mirrors POSTAL_WRITES_ENABLED.

    POSTAL_ESCALATE_ENABLED=true   # in Doppler paperclip/prd

Reuses the morning-briefing send path (Resend email + GHL SMS), so it inherits
BRIEFING_RECIPIENTS / MICHAEL_PHONE config and needs no new secrets.

Plan: ~/cd-ops/plans/paperclip_postal_agent_2026-06-22.md (Phase 5 — escalation)
"""

from __future__ import annotations

import html as _html
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Human-friendly labels for the categories that can escalate.
_CATEGORY_LABEL = {
    "intent_reply": "prospect",
    "lead_response": "prospect",
    "billing": "payment",
    "security": "account security",
}


def escalation_enabled() -> bool:
    """True iff POSTAL_ESCALATE_ENABLED is truthy. Default: disabled (shadow)."""
    return (os.environ.get("POSTAL_ESCALATE_ENABLED", "") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _summary_counts(items: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Group escalations by friendly label → list of account labels."""
    out: dict[str, list[str]] = {}
    for it in items:
        label = _CATEGORY_LABEL.get(it.get("category", ""), it.get("category", "other"))
        out.setdefault(label, []).append(it.get("account", "?"))
    return out


def build_sms(items: list[dict[str, Any]]) -> str:
    """One-line digest, e.g. '2 prospects (wd, avi), 1 payment (wd)'."""
    groups = _summary_counts(items)
    parts = []
    for label, accts in groups.items():
        n = len(accts)
        noun = label if n == 1 else (label + "s" if not label.endswith("y") else label[:-1] + "ies")
        parts.append(f"{n} {noun} ({', '.join(sorted(set(accts)))})")
    return f"🔥 Postal: {', '.join(parts)} need you. Check #revenue-sales / email."


def build_email_html(items: list[dict[str, Any]]) -> str:
    """Readable list of every escalated item, grouped by account."""
    by_acct: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        by_acct.setdefault(it.get("account", "?"), []).append(it)

    rows = []
    for acct in sorted(by_acct):
        rows.append(f'<h3 style="margin:18px 0 6px">📥 {_html.escape(acct)}</h3>')
        for it in by_acct[acct]:
            cat = _CATEGORY_LABEL.get(it.get("category", ""), it.get("category", ""))
            sender = _html.escape((it.get("sender") or "?")[:120])
            subject = _html.escape((it.get("subject") or "(no subject)")[:160])
            snippet = _html.escape((it.get("snippet") or "")[:240])
            rows.append(
                '<div style="margin:0 0 12px;padding:10px 12px;border-left:3px solid #e0a800;background:#fffdf5">'
                f'<div style="font-size:12px;color:#a07a00;text-transform:uppercase;letter-spacing:.04em">{_html.escape(cat)}</div>'
                f'<div><b>From:</b> {sender}</div>'
                f'<div><b>Subject:</b> {subject}</div>'
                + (f'<div style="color:#555;margin-top:4px">{snippet}</div>' if snippet else "")
                + "</div>"
            )
    body = "".join(rows)
    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:640px;margin:auto">'
        f'<h2 style="margin:0 0 4px">🔥 {len(items)} email(s) need you</h2>'
        '<p style="color:#666;margin:0 0 8px">Postal Agent flagged these as human-actionable. '
        "Prospects also posted to #revenue-sales; payments/security to #pit-wall.</p>"
        f"{body}</div>"
    )


def send_escalations(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Send one batched email + SMS for the sweep's escalated items.

    Never raises. Returns a small stats dict. When POSTAL_ESCALATE_ENABLED is
    off, logs what it WOULD send and sends nothing (shadow).
    """
    if not items:
        return {"escalated": 0, "sent": False}

    if not escalation_enabled():
        preview = [
            f"{it.get('account')}:{it.get('category')}:{(it.get('sender') or '')[:40]}"
            for it in items
        ]
        logger.info(
            "postal ESCALATION SHADOW (POSTAL_ESCALATE_ENABLED off): would alert %d item(s): %s",
            len(items), preview,
        )
        return {"escalated": len(items), "sent": False, "shadow": True}

    try:
        from rivers.shared.morning_briefing import send_briefing_email, send_briefing_sms
    except Exception as e:  # pragma: no cover - import guard
        logger.error("postal escalation: could not import send path: %s", e)
        return {"escalated": len(items), "sent": False, "error": str(e)}

    subject = f"🔥 Postal: {len(items)} email(s) need you"
    email_ok = sms_ok = False
    try:
        email_ok = bool(send_briefing_email(build_email_html(items), subject))
    except Exception as e:
        logger.error("postal escalation email failed: %s", e)
    try:
        sms_ok = bool(send_briefing_sms(build_sms(items)))
    except Exception as e:
        logger.error("postal escalation sms failed: %s", e)

    logger.info(
        "postal escalation sent: items=%d email=%s sms=%s", len(items), email_ok, sms_ok
    )
    return {"escalated": len(items), "sent": True, "email": email_ok, "sms": sms_ok}
