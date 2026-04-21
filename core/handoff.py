# AVO — AI Business Operating System
# Agent-to-Agent Handoff System
# Built live for Agent Empire Skool community
# Salesdroid — April 2026

"""Structured agent-to-agent handoffs.

Agents no longer run in isolation. When Atlas finishes a dealer brief,
Ryan Data gets it before his next run. When Tyler logs new prospects,
Jennifer prepares onboarding materials.

PostgreSQL table: agent_handoffs
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.database import execute_query, fetch_all
from services.errors import DatabaseError

logger = logging.getLogger(__name__)


def create_handoff(
    from_agent: str,
    to_agent: str,
    river: str,
    handoff_type: str,
    payload: Any,
    priority: str = "medium",
) -> Optional[int]:
    """Create a handoff from one agent to another.

    Args:
        from_agent: e.g. "atlas"
        to_agent: e.g. "ryan_data"
        river: e.g. "autointelligence"
        handoff_type: e.g. "dealer_brief", "content_review", "prospect_alert"
        payload: dict — the actual context being passed
        priority: high | medium | low

    Returns:
        Handoff ID if created, None on error.
    """
    try:
        payload_json = json.dumps(payload, default=str)
        rows = fetch_all(
            "INSERT INTO agent_handoffs "
            "(from_agent, to_agent, river, handoff_type, payload, priority, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'pending') "
            "RETURNING id",
            (from_agent, to_agent, river, handoff_type, payload_json, priority),
        )
        handoff_id: Optional[int] = int(rows[0][0]) if rows and rows[0] else None
        logger.info(
            "[Handoff] #%s %s → %s (%s/%s) priority=%s",
            handoff_id, from_agent, to_agent, river, handoff_type, priority,
        )
        return handoff_id
    except DatabaseError as e:
        logger.warning("[Handoff] create_handoff failed: %s", e)
        return None


def get_pending_handoffs(agent_name: str) -> List[Dict[str, Any]]:
    """Get all pending handoffs assigned to an agent.

    Returns:
        List of handoff dicts with parsed payload.
    """
    try:
        rows = fetch_all(
            "SELECT id, from_agent, to_agent, river, handoff_type, "
            "payload, priority, status, created_at "
            "FROM agent_handoffs "
            "WHERE to_agent = %s AND status = 'pending' "
            "ORDER BY CASE priority "
            "  WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, "
            "created_at ASC",
            (agent_name,),
        )
        results = []
        for row in rows:
            try:
                payload = json.loads(row[5]) if row[5] else {}
            except json.JSONDecodeError:
                payload = {"raw": row[5]}
            results.append({
                "id": row[0],
                "from_agent": row[1],
                "to_agent": row[2],
                "river": row[3],
                "handoff_type": row[4],
                "payload": payload,
                "priority": row[6],
                "status": row[7],
                "created_at": str(row[8]),
            })
        return results
    except DatabaseError as e:
        logger.warning("[Handoff] get_pending_handoffs failed for %s: %s", agent_name, e)
        return []


def pick_up_handoff(handoff_id: int) -> bool:
    """Mark a handoff as picked up by the receiving agent."""
    try:
        execute_query(
            "UPDATE agent_handoffs SET status = 'picked_up', picked_up_at = NOW() "
            "WHERE id = %s AND status = 'pending'",
            (handoff_id,),
        )
        logger.info("[Handoff] Picked up handoff #%s", handoff_id)
        return True
    except DatabaseError as e:
        logger.warning("[Handoff] pick_up_handoff failed for #%s: %s", handoff_id, e)
        return False


def complete_handoff(handoff_id: int) -> bool:
    """Mark a handoff as completed."""
    try:
        execute_query(
            "UPDATE agent_handoffs SET status = 'complete', completed_at = NOW() "
            "WHERE id = %s",
            (handoff_id,),
        )
        logger.info("[Handoff] Completed handoff #%s", handoff_id)
        return True
    except DatabaseError as e:
        logger.warning("[Handoff] complete_handoff failed for #%s: %s", handoff_id, e)
        return False


def build_handoff_context(agent_name: str) -> str:
    """Build a handoff injection string for an agent's task context.

    Called at the START of every agent run, after memory injection.
    Returns context about pending handoffs to inform the agent's work.
    """
    handoffs = get_pending_handoffs(agent_name)
    if not handoffs:
        return ""

    lines = [f"HANDOFFS PENDING ({len(handoffs)}):"]
    for h in handoffs:
        payload_summary = str(h["payload"])[:200]
        lines.append(
            f"  [{h['priority'].upper()}] From {h['from_agent']}: "
            f"{h['handoff_type']} — {payload_summary}"
        )
        pick_up_handoff(h["id"])

    return " ".join(lines)


def get_handoff_counts(agent_name: Optional[str] = None) -> Dict[str, int]:
    """Get handoff counts for dashboard display."""
    try:
        if agent_name:
            rows = fetch_all(
                "SELECT status, COUNT(*) FROM agent_handoffs "
                "WHERE to_agent = %s GROUP BY status",
                (agent_name,),
            )
        else:
            rows = fetch_all(
                "SELECT status, COUNT(*) FROM agent_handoffs GROUP BY status"
            )
        return {row[0]: row[1] for row in rows}
    except DatabaseError:
        return {}
