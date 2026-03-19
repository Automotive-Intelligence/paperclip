"""
services/approval_queue.py — Risk-Based Approval Gate for AIBOS Artifacts

The approval queue is the policy enforcement layer between an agent producing
an artifact and that artifact reaching the real world. It is NOT a bottleneck —
it is a trust escalator.

Routing logic:
  auto_approved  → low risk + confidence >= 0.75  → dispatch immediately
  pending_approval → medium risk OR low-confidence low-risk → queue for 1-click human approval
  escalated      → high risk OR moral-gate failure → escalate, never auto-dispatch

All queue state is stored in Postgres for durability/auditability.
In-memory state is never the source of truth.
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

import datetime
import json
import logging
from typing import Any, Dict, List, Optional

from services.artifact import Artifact
from services.database import execute_query, fetch_all
from services.errors import DatabaseError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Persist / load helpers
# ---------------------------------------------------------------------------

def _json_safe(value: Any) -> str:
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return json.dumps(str(value))


def persist_artifact(artifact: Artifact) -> None:
    """
    Upsert an artifact row in the `artifacts` table.

    Uses ON CONFLICT(artifact_id) DO UPDATE so callers can call this both
    on initial queue and on status transitions (approve, reject, dispatch).
    """
    try:
        execute_query(
            """
            INSERT INTO artifacts
                (artifact_id, agent_id, business_key, artifact_type, audience,
                 intent, content, subject, channel_candidates, confidence,
                 risk_level, requires_human_approval, metadata, created_at, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (artifact_id) DO UPDATE SET
                status                  = EXCLUDED.status,
                requires_human_approval = EXCLUDED.requires_human_approval
            """,
            (
                artifact.artifact_id,
                artifact.agent_id,
                artifact.business_key,
                artifact.artifact_type,
                artifact.audience,
                artifact.intent,
                artifact.content,
                artifact.subject,
                _json_safe(artifact.channel_candidates),
                artifact.confidence,
                artifact.risk_level,
                artifact.requires_human_approval,
                _json_safe(artifact.metadata),
                artifact.created_at,
                artifact.status,
            ),
        )
    except DatabaseError as exc:
        logger.error("[queue] ✗ persist_artifact failed for %s: %s", artifact.artifact_id, exc)
        raise


def _update_artifact_status(artifact_id: str, status: str) -> None:
    try:
        execute_query(
            "UPDATE artifacts SET status = %s WHERE artifact_id = %s",
            (status, artifact_id),
        )
    except DatabaseError as exc:
        logger.error("[queue] ✗ status update failed for %s: %s", artifact_id, exc)
        raise


def _persist_approval_record(
    artifact_id: str,
    decision: str,
    reviewer: str,
    reason: Optional[str] = None,
) -> None:
    try:
        execute_query(
            """
            INSERT INTO artifact_approvals
                (artifact_id, decision, reviewer, reason, decided_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (artifact_id, decision, reviewer, reason, datetime.datetime.utcnow()),
        )
    except DatabaseError as exc:
        logger.warning("[queue] ✗ approval record persist failed: %s", exc)


def _row_to_dict(row: tuple) -> Dict[str, Any]:
    """Convert a raw DB artifacts row to a plain dict."""
    keys = [
        "artifact_id", "agent_id", "business_key", "artifact_type", "audience",
        "intent", "content", "subject", "channel_candidates", "confidence",
        "risk_level", "requires_human_approval", "metadata", "created_at", "status",
    ]
    d = dict(zip(keys, row))
    # Deserialize JSON columns
    for col in ("channel_candidates", "metadata"):
        try:
            if isinstance(d[col], str):
                d[col] = json.loads(d[col])
        except (json.JSONDecodeError, KeyError):
            pass
    return d


# ---------------------------------------------------------------------------
# Public queue API
# ---------------------------------------------------------------------------

def queue_artifact(artifact: Artifact) -> str:
    """
    Persist an artifact into the approval queue and return its artifact_id.

    The artifact's status field (set by create_artifact) determines the lane:
      - "auto_approved"     → will be dispatched by the caller immediately
      - "pending_approval"  → waits in queue for human 1-click approve/reject
      - "escalated"         → flagged for manager review; cannot be auto-approved
    """
    persist_artifact(artifact)
    logger.info(
        "[queue] artifact=%s type=%s risk=%s status=%s confidence=%.2f",
        artifact.artifact_id, artifact.artifact_type,
        artifact.risk_level, artifact.status, artifact.confidence,
    )
    return artifact.artifact_id


def get_pending() -> List[Dict[str, Any]]:
    """Return all artifacts currently in pending_approval status, oldest first."""
    try:
        rows = fetch_all(
            "SELECT artifact_id, agent_id, business_key, artifact_type, audience, "
            "       intent, content, subject, channel_candidates, confidence, "
            "       risk_level, requires_human_approval, metadata, created_at, status "
            "FROM artifacts "
            "WHERE status = 'pending_approval' "
            "ORDER BY created_at ASC",
            (),
        )
        return [_row_to_dict(r) for r in rows]
    except DatabaseError as exc:
        logger.error("[queue] get_pending failed: %s", exc)
        return []


def get_escalated() -> List[Dict[str, Any]]:
    """Return all artifacts in escalated status."""
    try:
        rows = fetch_all(
            "SELECT artifact_id, agent_id, business_key, artifact_type, audience, "
            "       intent, content, subject, channel_candidates, confidence, "
            "       risk_level, requires_human_approval, metadata, created_at, status "
            "FROM artifacts "
            "WHERE status = 'escalated' "
            "ORDER BY created_at ASC",
            (),
        )
        return [_row_to_dict(r) for r in rows]
    except DatabaseError as exc:
        logger.error("[queue] get_escalated failed: %s", exc)
        return []


def get_artifact_record(artifact_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single artifact by ID. Returns None if not found."""
    try:
        rows = fetch_all(
            "SELECT artifact_id, agent_id, business_key, artifact_type, audience, "
            "       intent, content, subject, channel_candidates, confidence, "
            "       risk_level, requires_human_approval, metadata, created_at, status "
            "FROM artifacts "
            "WHERE artifact_id = %s",
            (artifact_id,),
        )
        return _row_to_dict(rows[0]) if rows else None
    except DatabaseError as exc:
        logger.error("[queue] get_artifact_record failed for %s: %s", artifact_id, exc)
        return None


def list_artifacts(
    *,
    business_key: Optional[str] = None,
    agent_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Query artifacts with optional filters.

    Args:
        business_key: Filter by business.
        agent_id:     Filter by producing agent.
        status:       Filter by status (e.g. "pending_approval", "delivered").
        limit:        Max rows to return (default 50, max 200).
    """
    limit = min(max(1, limit), 200)
    conditions: List[str] = []
    params: List[Any] = []

    if business_key:
        conditions.append("business_key = %s")
        params.append(business_key)
    if agent_id:
        conditions.append("agent_id = %s")
        params.append(agent_id)
    if status:
        conditions.append("status = %s")
        params.append(status)

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    try:
        rows = fetch_all(
            f"SELECT artifact_id, agent_id, business_key, artifact_type, audience, "
            f"       intent, content, subject, channel_candidates, confidence, "
            f"       risk_level, requires_human_approval, metadata, created_at, status "
            f"FROM artifacts {where_clause} "
            f"ORDER BY created_at DESC LIMIT %s",
            tuple(params) + (limit,),
        )
        return [_row_to_dict(r) for r in rows]
    except DatabaseError as exc:
        logger.error("[queue] list_artifacts failed: %s", exc)
        return []


def approve_artifact(artifact_id: str, reviewer: str) -> bool:
    """
    Mark an artifact as approved and ready for dispatch.

    Returns True if the status transition succeeded, False otherwise.
    This does NOT dispatch — the caller (endpoint) must then call dispatch_artifact().
    """
    record = get_artifact_record(artifact_id)
    if not record:
        logger.warning("[queue] approve: artifact %s not found", artifact_id)
        return False
    if record["status"] not in ("pending_approval", "escalated"):
        logger.warning(
            "[queue] approve rejected — artifact %s is in status '%s'",
            artifact_id, record["status"],
        )
        return False

    _update_artifact_status(artifact_id, "approved")
    _persist_approval_record(artifact_id, "approved", reviewer)
    logger.info("[queue] ✓ artifact=%s approved by %s", artifact_id, reviewer)
    return True


def reject_artifact(artifact_id: str, reviewer: str, reason: str = "") -> bool:
    """
    Mark an artifact as rejected. The artifact will not be dispatched.

    Returns True if the transition succeeded.
    """
    record = get_artifact_record(artifact_id)
    if not record:
        logger.warning("[queue] reject: artifact %s not found", artifact_id)
        return False

    _update_artifact_status(artifact_id, "rejected")
    _persist_approval_record(artifact_id, "rejected", reviewer, reason)
    logger.info("[queue] ✓ artifact=%s rejected by %s reason=%r", artifact_id, reviewer, reason)
    return True
