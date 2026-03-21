"""
tools/email_templates.py - Templated outbound email composer and validator.

This module standardizes email structure (header/body/footer), validates rendered
content, and provides deterministic template keys for reporting.
"""

import os
import re
from typing import Dict, List


_BUSINESS_BRANDING = {
    "aiphoneguy": {
        "display_name": "The AI Phone Guy",
        "tagline": "Never miss a revenue call",
        "signature": "The AI Phone Guy Team",
        "cta_label": "Worth a quick look?",
    },
    "callingdigital": {
        "display_name": "Calling Digital",
        "tagline": "Digital growth with AI execution",
        "signature": "Calling Digital Team",
        "cta_label": "Open to a quick audit?",
    },
    "autointelligence": {
        "display_name": "Automotive Intelligence",
        "tagline": "AI readiness for modern dealerships",
        "signature": "Automotive Intelligence Team",
        "cta_label": "Open to a free readiness assessment?",
    },
}

_TEMPLATE_BY_AGENT = {
    "tyler": "cold_first_touch_v1",
    "marcus": "cold_first_touch_v1",
    "ryan_data": "cold_first_touch_v1",
}


def _clean_text(value: str) -> str:
    text = (value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def template_key_for(agent_name: str) -> str:
    return _TEMPLATE_BY_AGENT.get((agent_name or "").strip().lower(), "cold_first_touch_v1")


def _brand_for(business_key: str) -> Dict[str, str]:
    return _BUSINESS_BRANDING.get((business_key or "").strip().lower(), {
        "display_name": "Paperclip",
        "tagline": "AI-native revenue operations",
        "signature": "Paperclip Team",
        "cta_label": "Worth a quick look?",
    })


def _fallback_subject(prospect: dict, business_key: str) -> str:
    reason = _clean_text(prospect.get("reason", ""))
    base = "quick idea"
    if business_key == "aiphoneguy":
        base = "missed calls"
    elif business_key == "callingdigital":
        base = "quick audit"
    elif business_key == "autointelligence":
        base = "ai readiness"
    if reason:
        return f"{base}: {reason[:42]}".strip()
    return base


def validate_rendered_email(subject: str, body_text: str) -> List[str]:
    """Return validation issues. Empty list means valid content."""
    issues: List[str] = []
    s = (subject or "").strip()
    b = (body_text or "").strip()

    if not s:
        issues.append("missing_subject")
    if not b:
        issues.append("missing_body")

    unresolved_tokens = ["{{", "}}", "[[", "]]", "<%", "%>"]
    if any(t in s or t in b for t in unresolved_tokens):
        issues.append("unresolved_tokens")

    lowered = f"{s} {b}".lower()
    suspicious_markers = [
        "lorem ipsum",
        "not found",
        "unknown",
        "n/a",
        "placeholder",
        "[first name]",
        "[company]",
    ]
    if any(marker in lowered for marker in suspicious_markers):
        issues.append("placeholder_or_unverified_content")

    if len(b.split()) < 20:
        issues.append("body_too_short")

    return issues


def compose_templated_email(prospect: dict, business_key: str, agent_name: str) -> Dict[str, object]:
    """
    Build a standardized first-touch email from agent output + template wrapper.

    Returns:
      {
        subject: str,
        body_text: str,
        body_html: str,
        template_key: str,
        valid: bool,
        issues: list[str],
      }
    """
    brand = _brand_for(business_key)
    template_key = template_key_for(agent_name)

    company = _clean_text(prospect.get("business_name", "there"))
    contact_name = _clean_text(prospect.get("contact_name", ""))
    first_name = contact_name.split()[0] if contact_name else "there"

    subject = _clean_text(prospect.get("subject", "")) or _fallback_subject(prospect, business_key)
    body_core = (prospect.get("body") or "").strip()
    if not body_core:
        reason = _clean_text(prospect.get("reason", "a growth opportunity in your market"))
        body_core = (
            f"I noticed {reason}. "
            "If helpful, I can share a short plan with concrete next steps."
        )

    header = f"Hi {first_name},"
    footer = (
        f"\n\n{brand['cta_label']}\n"
        f"- {brand['signature']}\n"
        f"{brand['display_name']} | {brand['tagline']}"
    )

    body_text = f"{header}\n\n{body_core.strip()}{footer}".strip()
    body_html = body_text.replace("\n", "<br>")

    issues = validate_rendered_email(subject, body_text)
    valid = len(issues) == 0

    return {
        "subject": subject,
        "body_text": body_text,
        "body_html": body_html,
        "template_key": template_key,
        "valid": valid,
        "issues": issues,
    }


def strict_template_validation_enabled() -> bool:
    val = (os.getenv("EMAIL_TEMPLATE_STRICT") or "false").strip().lower()
    return val in {"1", "true", "yes", "on"}
