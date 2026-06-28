"""Postal Agent classifier — rules-first with LLM fallback.

Per Postal Agent plan Q3.1=B (2026-06-22). Rules handle the easy 80%
(known senders, obvious subjects). Anything ambiguous falls through to
an LLM call for nuanced classification.

Categories (locked v1):
    intent_reply       — DataMoon-tagged sender, intent-signal platform reply
    lead_response      — reply to outbound campaign / cold-open thread
    billing            — invoices, receipts, payment alerts, expirations
    security           — 2FA, password resets, breach notices, sign-in alerts
    newsletter         — mailing lists, substack, beehiiv, etc.
    transactional      — order confirmations, shipping, app-generated receipts
    meeting            — calendar invites, Calendly, scheduling acks
    vendor_alert       — platform notifications (Smartlead/Instantly/Loops/Twenty/Stripe alerts NOT covered by billing)
    junk               — clearly cold spam, no relevance
    other              — unclassifiable; LLM saw it and still couldn't decide

Returns (category, confidence_float_0_to_1, reason_string, used_llm_bool).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from config.llm import get_llm_research

logger = logging.getLogger(__name__)


# ----- Allowed category set -----

CATEGORIES = {
    "intent_reply",
    "lead_response",
    "billing",
    "security",
    "newsletter",
    "transactional",
    "meeting",
    "vendor_alert",
    "junk",
    "other",
}


# ----- Rule-set: sender domain → category -----
# Substring match against the sender's email domain (case-insensitive).
DOMAIN_RULES: list[tuple[str, str]] = [
    # Intent / lead-data platforms
    ("datamoon.com", "intent_reply"),
    ("datamoon.io", "intent_reply"),
    ("kixie.com", "intent_reply"),
    # Billing
    ("stripe.com", "billing"),
    ("intuit.com", "billing"),
    ("quickbooks.com", "billing"),
    ("railway.app", "billing"),
    ("vercel.com", "billing"),
    ("doppler.com", "billing"),
    ("anthropic.com", "billing"),
    ("openai.com", "billing"),
    # Security
    ("accounts.google.com", "security"),
    ("noreply@google.com", "security"),
    ("notify.cloudflare.com", "security"),
    ("github.com", "security"),
    # Newsletters
    ("mail.beehiiv.com", "newsletter"),
    ("substack.com", "newsletter"),
    ("mail.notion.so", "newsletter"),
    ("email.rocketmoney.com", "newsletter"),
    ("mail.usaa.com", "newsletter"),
    # Vendor platform alerts
    ("smartlead.ai", "vendor_alert"),
    ("instantly.ai", "vendor_alert"),
    ("loops.so", "vendor_alert"),
    ("twenty.com", "vendor_alert"),
    ("hubspot.com", "vendor_alert"),
    # Meeting / scheduling
    ("calendly.com", "meeting"),
    ("cal.com", "meeting"),
    ("savvycal.com", "meeting"),
    ("calendar-notification@google.com", "meeting"),
]


# ----- Rule-set: subject regex → category -----
SUBJECT_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(invoice|receipt|payment|past\s*due|invoice\s*paid)\b", re.I), "billing"),
    (re.compile(r"\b(2FA|verification\s*code|password\s*reset|sign-?in\s*alert)\b", re.I), "security"),
    (re.compile(r"\b(new\s*sign-?in|unusual\s*activity|account\s*locked)\b", re.I), "security"),
    (re.compile(r"\b(meeting|invite|scheduled\s*for|calendar)\b", re.I), "meeting"),
    (re.compile(r"\b(unsubscribe|opt-?out)\b", re.I), "newsletter"),
    (re.compile(r"^\s*Re:\s", re.I), "lead_response"),  # reply pattern; LLM may override
]


# ----- Escalation: which inbounds a human must act on TODAY -----
#
# Used by the Postal Agent to decide whether to push a batched SMS+email alert
# (services/postal_escalation.py). Deliberately tight to avoid alert fatigue:
# real prospects always; money/security only when the subject signals a genuine
# problem; and known automated/dev-bot senders never escalate even though their
# category is billing/security.

# Senders that are automated noise — never SMS-worthy.
ESCALATE_SUPPRESS_DOMAINS = (
    "github.com", "vercel.com", "railway.app", "doppler.com", "anthropic.com",
    "openai.com", "cloudflare.com", "accounts.google.com", "noreply@google.com",
)

# Categories that are always human-actionable (real revenue).
ESCALATE_CATEGORIES = {"intent_reply", "lead_response"}

# A billing email escalates only if it reads like a PROBLEM, not a routine
# receipt / "payment received".
_BILLING_URGENT_RE = re.compile(
    r"\b(past\s*due|over\s*due|payment\s*failed|failed\s*payment|declined|"
    r"action\s*required|final\s*notice|card\s*(?:expir|declin)|suspend)\b",
    re.I,
)
# A security email escalates only if it's a genuine account event.
_SECURITY_URGENT_RE = re.compile(
    r"\b(unusual\s*activity|new\s*sign-?in|account\s*(?:locked|suspended)|"
    r"suspicious|unauthorized|compromis)\b",
    re.I,
)


def should_escalate(
    category: str, email_meta: dict[str, Any], confidence: float = 1.0
) -> tuple[bool, str]:
    """Decide whether an inbound warrants a direct alert to Michael.

    Returns (escalate, reason). Tuned conservatively: prospects always; billing
    /security only on real-problem subjects; automated/dev-bot senders never.
    """
    sender = (email_meta.get("sender") or "").lower()
    subject = email_meta.get("subject") or ""

    for d in ESCALATE_SUPPRESS_DOMAINS:
        if d in sender:
            return False, f"suppressed automated sender: {d}"

    if category in ESCALATE_CATEGORIES:
        return True, f"prospect ({category})"
    if category == "billing" and _BILLING_URGENT_RE.search(subject):
        return True, "payment problem"
    if category == "security" and _SECURITY_URGENT_RE.search(subject):
        return True, "account security"
    return False, "not human-actionable"


# ----- Public API -----

def classify(email_meta: dict[str, Any]) -> tuple[str, float, str, bool]:
    """Classify an email by sender + subject + snippet.

    Args:
        email_meta: {sender, subject, snippet, to_recipients?, account_label?}

    Returns:
        (category, confidence, reason, used_llm)
    """
    sender = (email_meta.get("sender") or "").lower()
    subject = (email_meta.get("subject") or "").strip()
    snippet = (email_meta.get("snippet") or "").strip()

    # Pass 1: domain rules (high confidence)
    for domain, cat in DOMAIN_RULES:
        if domain in sender:
            return cat, 0.95, f"sender-domain match: {domain}", False

    # Pass 2: subject regex rules (medium confidence)
    for rx, cat in SUBJECT_RULES:
        if rx.search(subject):
            return cat, 0.70, f"subject regex: {rx.pattern}", False

    # Pass 3: LLM fallback (variable confidence; the LLM tells us)
    try:
        return _classify_llm(sender, subject, snippet, email_meta) + (True,)
    except Exception as e:
        logger.exception("postal classifier LLM fallback failed")
        return "other", 0.0, f"llm_fallback_error: {e}", True


def _classify_llm(sender: str, subject: str, snippet: str, email_meta: dict[str, Any]) -> tuple[str, float, str]:
    """LLM fallback. Returns (category, confidence, reason)."""
    llm = get_llm_research()

    cats_list = ", ".join(sorted(CATEGORIES))
    prompt = f"""You classify inbound emails for a B2B marketing/sales agent platform.

Categories (pick exactly ONE): {cats_list}

Definitions:
- intent_reply: replies from intent-data tools or in-market accounts that showed buying signals
- lead_response: replies from individual prospects to outbound campaigns
- billing: invoices, receipts, payment alerts, subscription expirations
- security: 2FA codes, password resets, breach notices, sign-in alerts
- newsletter: mailing lists, substack/beehiiv, marketing emails not directed at the recipient personally
- transactional: order confirmations, shipping notifications, app-generated receipts (not invoices)
- meeting: calendar invites, scheduling tools (Calendly), meeting confirmations
- vendor_alert: platform notifications from tools the recipient uses (Smartlead, Instantly, GitHub, etc.) that aren't billing
- junk: cold spam, no clear relevance
- other: doesn't fit any of the above

Email:
From: {sender}
Subject: {subject}
Snippet: {snippet[:500]}

Respond ONLY with a single JSON object on one line:
{{"category": "...", "confidence": 0.0-1.0, "reason": "short explanation under 80 chars"}}
"""

    raw = llm.call(prompt) if hasattr(llm, "call") else str(llm.complete(prompt))
    raw = (raw or "").strip()

    # Be defensive: LLMs sometimes wrap JSON in code fences
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()

    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract a JSON object from the response
        m = re.search(r"\{.*?\}", raw, re.DOTALL)
        if not m:
            raise ValueError(f"LLM did not return JSON: {raw[:200]}")
        obj = json.loads(m.group(0))

    cat = (obj.get("category") or "other").strip()
    if cat not in CATEGORIES:
        cat = "other"
    conf = float(obj.get("confidence", 0.0))
    conf = max(0.0, min(1.0, conf))
    reason = (obj.get("reason") or "")[:200]
    return cat, conf, f"llm: {reason}"
