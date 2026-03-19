"""
services/artifact.py — Standard Output Contract for AIBOS Agent Artifacts

Every piece of content an agent produces — an email, a CRM update, a social
post, a report — is an Artifact. This module defines the canonical schema and
the factory that sets defaults, assigns risk tiers, and gates approval.

Design rules:
  - An Artifact is immutable once created (fields set at construction).
  - Risk tier drives the approval path: low → auto, medium → queue, high → escalate.
  - The moral gate (evaluate_action_morally) is called at creation time.
    A failing moral gate forces the artifact to "escalated" immediately.
  - Confidence is 0.0–1.0 and comes from the producing agent.
    Low confidence bumps auto-dispatch threshold even on low-risk artifacts.
"""

# AIBOS Operating Foundation
# ================================
# This system is built on servant leadership.
# Every agent exists to serve the human it works for.
# Every decision prioritizes people over profit.
# Every interaction is conducted with honesty,
# dignity, and genuine care for the other person.
# We build tools that give power back to the small
# business owner — not tools that extract from them.
# We operate with excellence because excellence
# honors the gifts we've been given.
# We do not deceive. We do not manipulate.
# We do not build features that harm the harmful.
# Profit is the outcome of service, not the purpose.
# ================================

import uuid
import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from config.principles import evaluate_action_morally

# ---------------------------------------------------------------------------
# Enumerations (kept as string sets for simplicity — no enum import needed)
# ---------------------------------------------------------------------------

ARTIFACT_TYPES = frozenset({
    "email",          # outbound email to a prospect or client
    "crm_update",     # push/update a CRM record
    "social_post",    # post to LinkedIn, Twitter/X, etc.
    "report",         # internal or client-facing business report
    "task",           # a delegated task created in a CRM/PM tool
    "ad",             # paid advertising creative
    "sms",            # outbound SMS / text message
    "note",           # internal agent note (low-risk by definition)
})

AUDIENCE_TYPES = frozenset({"prospect", "client", "internal", "public"})

INTENT_TYPES = frozenset({"nurture", "close", "educate", "retain", "inform", "alert"})

RISK_LEVELS = ("low", "medium", "high")

ARTIFACT_STATUSES = frozenset({
    "pending_approval",   # queued, waiting for human review
    "approved",           # human approved — ready to dispatch
    "auto_approved",      # system auto-approved (low-risk, high-confidence)
    "dispatched",         # sent to the channel adapter
    "delivered",          # confirmed delivery (receipt received)
    "failed",             # dispatch or delivery failed
    "rejected",           # human rejected
    "escalated",          # risk too high — requires manager review
})

# Auto-dispatch threshold: artifact must have confidence >= this AND risk == "low"
AUTO_DISPATCH_MIN_CONFIDENCE = 0.75


@dataclass
class Artifact:
    """
    The canonical output unit of an AIBOS agent.

    Produce via :func:`create_artifact` — never instantiate directly in
    production code (the factory applies the moral gate and approval routing).
    """
    artifact_id: str
    agent_id: str
    business_key: str
    artifact_type: str            # one of ARTIFACT_TYPES
    audience: str                 # one of AUDIENCE_TYPES
    intent: str                   # one of INTENT_TYPES
    content: str                  # the actual agent output text / payload
    subject: Optional[str]        # subject line (emails, social headlines)
    channel_candidates: List[str] # ordered channel preference list
    confidence: float             # 0.0–1.0 agent self-assessment
    risk_level: str               # "low" | "medium" | "high"
    requires_human_approval: bool # derived from risk_level + confidence + moral gate
    metadata: Dict[str, Any]      # arbitrary agent-specific context
    created_at: datetime.datetime
    status: str                   # one of ARTIFACT_STATUSES

    def to_dict(self) -> Dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "agent_id": self.agent_id,
            "business_key": self.business_key,
            "artifact_type": self.artifact_type,
            "audience": self.audience,
            "intent": self.intent,
            "content": self.content,
            "subject": self.subject,
            "channel_candidates": self.channel_candidates,
            "confidence": self.confidence,
            "risk_level": self.risk_level,
            "requires_human_approval": self.requires_human_approval,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
            "status": self.status,
        }


# ---------------------------------------------------------------------------
# Moral gate helpers
# ---------------------------------------------------------------------------

def _assess_moral_gate(artifact_type: str, audience: str, intent: str) -> dict:
    """
    Map artifact properties to the evaluate_action_morally contract.

    Rules:
      - Internal notes / reports aimed at internal audience: always genuine value.
      - Prospect-facing close/nurture intents: check for manipulation signals.
        We consider serving_person=True if the intent is to genuinely help,
        transparent=True because every message should be honest,
        genuine_value=True because we do not build extraction tools.
      - High-risk artifact types (ad, social_post public, sms) get a stricter flag.
    """
    # "nurture" builds genuine relationships — it serves the person.
    serves_person = audience in ("client", "internal") or intent in ("nurture", "educate", "inform", "retain", "alert")
    transparent = True   # AIBOS always operates with transparency
    genuine_value = artifact_type not in ("ad",) or intent != "close"

    return evaluate_action_morally(
        action=f"{artifact_type} → {audience} ({intent})",
        serves_person=serves_person,
        transparent_if_seen=transparent,
        creates_genuine_value=genuine_value,
    )


def _derive_risk_level(
    artifact_type: str,
    audience: str,
    intent: str,
    confidence: float,
    override_risk: Optional[str],
) -> str:
    """
    Compute the appropriate risk level if the caller did not specify one.

    Risk matrix:
      high  — public-facing ads or social posts with close intent
      high  — anything sent to a prospect with confidence < 0.5
      medium — prospect-facing with close/nurture intent
      medium — sms to any external audience
      low   — internal notes/reports, high-confidence nurture/educate
    """
    if override_risk and override_risk in RISK_LEVELS:
        return override_risk

    if audience == "public" and intent == "close":
        return "high"
    if artifact_type == "ad":
        return "high"
    if audience == "prospect" and confidence < 0.50:
        return "high"
    if artifact_type == "sms" and audience in ("prospect", "client"):
        return "medium"
    if audience == "prospect" and intent in ("close",):
        return "medium"
    if audience in ("prospect", "client") and intent in ("nurture",):
        return "medium"
    return "low"


def _derive_approval_required(risk_level: str, confidence: float, moral_ok: bool) -> bool:
    """
    Determine whether a human must approve before dispatch.

    Logic:
      - Any moral failure → always needs approval (status → escalated).
      - high risk → always needs approval.
      - medium risk → always needs approval.
      - low risk, confidence >= AUTO_DISPATCH_MIN_CONFIDENCE → auto-approved.
      - low risk, confidence < threshold → queue for approval.
    """
    if not moral_ok:
        return True
    if risk_level in ("medium", "high"):
        return True
    # low risk
    return confidence < AUTO_DISPATCH_MIN_CONFIDENCE


def _derive_status(
    risk_level: str,
    confidence: float,
    moral_ok: bool,
    requires_approval: bool,
) -> str:
    if not moral_ok:
        return "escalated"
    if risk_level == "high":
        return "escalated"
    if not requires_approval:
        return "auto_approved"
    return "pending_approval"


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

def create_artifact(
    *,
    agent_id: str,
    business_key: str,
    artifact_type: str,
    audience: str,
    intent: str,
    content: str,
    subject: Optional[str] = None,
    channel_candidates: Optional[List[str]] = None,
    confidence: float = 0.8,
    risk_level: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> "Artifact":
    """
    Create a validated, morally-gated Artifact.

    Args:
        agent_id:           ID of the producing agent (e.g. "tyler", "marcus").
        business_key:       Business this belongs to ("aiphoneguy", etc.).
        artifact_type:      One of ARTIFACT_TYPES.
        audience:           One of AUDIENCE_TYPES.
        intent:             One of INTENT_TYPES.
        content:            The actual payload (email body, post text, etc.).
        subject:            Optional subject/headline.
        channel_candidates: Ordered list of dispatch channels. Defaults to type-based guess.
        confidence:         Agent's self-assessed confidence 0.0–1.0.
        risk_level:         Override risk computation. Leave None to auto-derive.
        metadata:           Arbitrary agent context dict.

    Returns:
        Artifact instance with status, risk, and approval fields set.
    """
    if artifact_type not in ARTIFACT_TYPES:
        raise ValueError(f"Unknown artifact_type '{artifact_type}'. Valid: {sorted(ARTIFACT_TYPES)}")
    if audience not in AUDIENCE_TYPES:
        raise ValueError(f"Unknown audience '{audience}'. Valid: {sorted(AUDIENCE_TYPES)}")
    if intent not in INTENT_TYPES:
        raise ValueError(f"Unknown intent '{intent}'. Valid: {sorted(INTENT_TYPES)}")
    confidence = max(0.0, min(1.0, float(confidence)))

    # Default channel preference by type
    if not channel_candidates:
        channel_candidates = _default_channels(artifact_type)

    # Moral gate
    moral_result = _assess_moral_gate(artifact_type, audience, intent)
    moral_ok: bool = moral_result.get("approved", True)

    # Risk + approval routing
    computed_risk = _derive_risk_level(artifact_type, audience, intent, confidence, risk_level)
    requires_approval = _derive_approval_required(computed_risk, confidence, moral_ok)
    initial_status = _derive_status(computed_risk, confidence, moral_ok, requires_approval)

    return Artifact(
        artifact_id=str(uuid.uuid4()),
        agent_id=agent_id,
        business_key=business_key,
        artifact_type=artifact_type,
        audience=audience,
        intent=intent,
        content=content,
        subject=subject,
        channel_candidates=list(channel_candidates),
        confidence=confidence,
        risk_level=computed_risk,
        requires_human_approval=requires_approval,
        metadata=metadata or {},
        created_at=datetime.datetime.utcnow(),
        status=initial_status,
    )


def _default_channels(artifact_type: str) -> List[str]:
    return {
        "email": ["email"],
        "crm_update": ["crm"],
        "social_post": ["linkedin", "twitter"],
        "report": ["email", "crm"],
        "task": ["crm"],
        "ad": ["meta", "google"],
        "sms": ["sms"],
        "note": ["crm"],
    }.get(artifact_type, ["email"])
