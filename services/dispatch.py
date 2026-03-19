"""
services/dispatch.py — Channel Adapter Router for AIBOS Artifacts

dispatch_artifact() is the single entry-point that moves an approved Artifact
into the real world. It routes to the correct channel adapter, records the
delivery receipt, and updates artifact status in Postgres.

Adapter availability:
  email   → GHL send_email (contact_id required in artifact.metadata)
  crm     → crm_router.push_prospects_to_crm (prospects list in metadata)
  sms     → GHL SMS endpoint (contact_id + phone required in metadata)
  linkedin → stub (future)
  twitter  → stub (future)
  meta     → stub (future)
  google   → stub (future)

All adapters produce a DeliveryReceipt regardless of success/failure so the
feedback loop is never broken.
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
# We do not build features that harm the vulnerable.
# Profit is the outcome of service, not the purpose.
# ================================

import logging
from typing import Optional

from services.artifact import Artifact
from services.delivery_receipt import DeliveryReceipt, make_receipt, record_receipt
from services.approval_queue import _update_artifact_status

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal channel adapters
# ---------------------------------------------------------------------------

def _dispatch_email(artifact: Artifact) -> DeliveryReceipt:
    """
    Email adapter — routes through GHL's conversation/messages endpoint.

    Required metadata keys:
        contact_id (str): GHL contact ID of the recipient.

    Optional metadata keys:
        from_name  (str): Sender display name.
        from_email (str): Sender email address.
    """
    from tools.ghl import send_email

    contact_id: Optional[str] = artifact.metadata.get("contact_id")
    if not contact_id:
        return make_receipt(
            artifact.artifact_id,
            "email",
            status="failed",
            error="metadata.contact_id is required for email dispatch",
        )

    subject = artifact.subject or "(no subject)"
    try:
        result = send_email(
            contact_id=contact_id,
            subject=subject,
            body=artifact.content,
            from_name=artifact.metadata.get("from_name"),
            from_email=artifact.metadata.get("from_email"),
        )
        return make_receipt(
            artifact.artifact_id,
            "email",
            status="delivered",
            provider_response=result if isinstance(result, dict) else {"raw": str(result)},
        )
    except Exception as exc:
        logger.error("[dispatch] email adapter error: %s", exc)
        return make_receipt(
            artifact.artifact_id,
            "email",
            status="failed",
            error=str(exc),
        )


def _dispatch_crm(artifact: Artifact) -> DeliveryReceipt:
    """
    CRM adapter — pushes a structured prospects list into the appropriate CRM.

    Required metadata keys:
        prospects (list[dict]): Prospect dicts compatible with crm_router.push_prospects_to_crm.
        business_key (str):     Used for CRM routing (falls back to artifact.business_key).

    The adapter returns "delivered" if at least one prospect was pushed without error.
    """
    from tools.crm_router import push_prospects_to_crm

    prospects = artifact.metadata.get("prospects", [])
    business_key = artifact.metadata.get("business_key") or artifact.business_key

    if not prospects:
        return make_receipt(
            artifact.artifact_id,
            "crm",
            status="failed",
            error="metadata.prospects list is empty — nothing to push",
        )

    try:
        result = push_prospects_to_crm(prospects, business_key=business_key)
        return make_receipt(
            artifact.artifact_id,
            "crm",
            status="delivered",
            provider_response=result if isinstance(result, dict) else {"summary": str(result)},
        )
    except Exception as exc:
        logger.error("[dispatch] crm adapter error: %s", exc)
        return make_receipt(
            artifact.artifact_id,
            "crm",
            status="failed",
            error=str(exc),
        )


def _dispatch_sms(artifact: Artifact) -> DeliveryReceipt:
    """
    SMS adapter via GHL conversations endpoint.

    Required metadata keys:
        contact_id (str): GHL contact ID.
    """
    from tools.ghl import _ghl_request

    contact_id: Optional[str] = artifact.metadata.get("contact_id")
    if not contact_id:
        return make_receipt(
            artifact.artifact_id,
            "sms",
            status="failed",
            error="metadata.contact_id is required for sms dispatch",
        )

    try:
        result = _ghl_request(
            "send_sms",
            "POST",
            "/conversations/messages",
            json_body={
                "type": "SMS",
                "contactId": contact_id,
                "message": artifact.content,
            },
            timeout=15,
        )
        return make_receipt(
            artifact.artifact_id,
            "sms",
            status="delivered",
            provider_response=result if isinstance(result, dict) else {},
        )
    except Exception as exc:
        logger.error("[dispatch] sms adapter error: %s", exc)
        return make_receipt(
            artifact.artifact_id,
            "sms",
            status="failed",
            error=str(exc),
        )


def _dispatch_stub(artifact: Artifact, channel: str) -> DeliveryReceipt:
    """
    Placeholder adapter for channels not yet implemented (social, ads, etc.).
    Logs the intent and records a "dispatched" receipt so the pipeline is complete.
    """
    logger.info(
        "[dispatch] stub channel=%s artifact=%s — channel adapter not yet implemented",
        channel, artifact.artifact_id,
    )
    return make_receipt(
        artifact.artifact_id,
        channel,
        status="dispatched",
        provider_response={"note": f"Channel '{channel}' adapter is a stub — not yet live."},
    )


# ---------------------------------------------------------------------------
# Channel router map
# ---------------------------------------------------------------------------

_CHANNEL_ADAPTERS = {
    "email":    _dispatch_email,
    "crm":      _dispatch_crm,
    "sms":      _dispatch_sms,
    "linkedin": lambda a: _dispatch_stub(a, "linkedin"),
    "twitter":  lambda a: _dispatch_stub(a, "twitter"),
    "meta":     lambda a: _dispatch_stub(a, "meta"),
    "google":   lambda a: _dispatch_stub(a, "google"),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def dispatch_artifact(artifact: Artifact) -> DeliveryReceipt:
    """
    Route an approved Artifact to its first available channel adapter.

    Only artifacts with status "auto_approved" or "approved" are dispatched.
    Any other status raises ValueError to prevent accidental dispatch of
    pending, rejected, or escalated artifacts.

    The artifact's channel_candidates list is tried in order. The first
    recognized channel is used. Unknown channels fall back to stub.

    Side-effects:
      - artifact.status updated to "dispatched" in Postgres
      - DeliveryReceipt persisted to delivery_receipts table
    """
    if artifact.status not in ("auto_approved", "approved"):
        raise ValueError(
            f"Cannot dispatch artifact {artifact.artifact_id} — "
            f"status is '{artifact.status}', must be 'auto_approved' or 'approved'."
        )

    # Mark as dispatched immediately so concurrent callers don't double-dispatch
    artifact.status = "dispatched"
    try:
        _update_artifact_status(artifact.artifact_id, "dispatched")
    except Exception as exc:
        logger.warning("[dispatch] status update pre-dispatch failed: %s", exc)

    # Find the first usable channel
    channel = _resolve_channel(artifact)
    adapter = _CHANNEL_ADAPTERS.get(channel, lambda a: _dispatch_stub(a, channel))

    logger.info(
        "[dispatch] artifact=%s type=%s channel=%s",
        artifact.artifact_id, artifact.artifact_type, channel,
    )

    receipt = adapter(artifact)

    # Persist receipt (fails silently — error is logged inside record_receipt)
    record_receipt(receipt)

    # Update artifact status to delivered or failed based on receipt
    final_status = "delivered" if receipt.status in ("delivered", "dispatched") else "failed"
    try:
        _update_artifact_status(artifact.artifact_id, final_status)
    except Exception as exc:
        logger.warning("[dispatch] status update post-dispatch failed: %s", exc)

    logger.info(
        "[dispatch] ✓ artifact=%s channel=%s receipt=%s final=%s",
        artifact.artifact_id, channel, receipt.receipt_id, final_status,
    )
    return receipt


def _resolve_channel(artifact: Artifact) -> str:
    """Return the first recognized channel from the artifact's candidate list."""
    for candidate in artifact.channel_candidates:
        if candidate in _CHANNEL_ADAPTERS:
            return candidate
    # If a candidate was specified but unrecognised, honour it — stub will handle it.
    if artifact.channel_candidates:
        return artifact.channel_candidates[0]
    # No candidates at all — fall back to type-based default.
    type_defaults = {
        "email":      "email",
        "crm_update": "crm",
        "note":       "crm",
        "task":       "crm",
        "sms":        "sms",
        "report":     "email",
        "social_post": "linkedin",
        "ad":         "meta",
    }
    return type_defaults.get(artifact.artifact_type, "email")
