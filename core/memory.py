# AVO — AI Business Operating System
# Heartbeat Memory — Agent continuity across runs
# Built live for Agent Empire Skool community
# Salesdroid — April 2026

"""Heartbeat memory for all AVO agents.

Every agent saves a daily_summary at the end of each run.
Before the next run, the agent reads yesterday's summary
so it never starts from zero.

PostgreSQL table: agent_memory
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from services.database import execute_query, fetch_all
from services.errors import DatabaseError

logger = logging.getLogger(__name__)


def save_memory(
    agent_name: str,
    memory_type: str,
    content: Any,
    river: str = "",
) -> bool:
    """Save a memory record for an agent.

    Args:
        agent_name: e.g. "tyler", "atlas"
        memory_type: daily_summary | prospects_found | content_produced |
                     directives_received | handoffs_sent | dealers_researched
        content: dict or list — serialized to JSON
        river: e.g. "aiphoneguy", "autointelligence"

    Returns:
        True if saved, False on error.
    """
    try:
        payload = json.dumps(content, default=str)
        execute_query(
            "INSERT INTO agent_memory (agent_name, river, memory_type, content) "
            "VALUES (%s, %s, %s, %s)",
            (agent_name, river, memory_type, payload),
        )
        logger.info("[Memory] Saved %s/%s for %s (%d chars)", memory_type, river, agent_name, len(payload))
        return True
    except DatabaseError as e:
        logger.warning("[Memory] save_memory failed for %s: %s", agent_name, e)
        return False


def get_last_memory(
    agent_name: str,
    memory_type: str,
) -> Optional[Dict[str, Any]]:
    """Get the most recent memory of a given type for an agent.

    Returns:
        Dict with keys: id, agent_name, river, memory_type, content (parsed JSON), created_at
        None if no memory found.
    """
    try:
        rows = fetch_all(
            "SELECT id, agent_name, river, memory_type, content, created_at "
            "FROM agent_memory "
            "WHERE agent_name = %s AND memory_type = %s "
            "ORDER BY created_at DESC LIMIT 1",
            (agent_name, memory_type),
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "id": row[0],
            "agent_name": row[1],
            "river": row[2],
            "memory_type": row[3],
            "content": json.loads(row[4]) if row[4] else {},
            "created_at": str(row[5]),
        }
    except (DatabaseError, json.JSONDecodeError) as e:
        logger.warning("[Memory] get_last_memory failed for %s: %s", agent_name, e)
        return None


def get_memory_window(
    agent_name: str,
    memory_type: str,
    days: int = 7,
) -> List[Dict[str, Any]]:
    """Get all memories of a given type within a time window.

    Returns:
        List of memory dicts, newest first.
    """
    try:
        rows = fetch_all(
            "SELECT id, agent_name, river, memory_type, content, created_at "
            "FROM agent_memory "
            "WHERE agent_name = %s AND memory_type = %s "
            "AND created_at >= NOW() - INTERVAL '%s days' "
            "ORDER BY created_at DESC",
            (agent_name, memory_type, days),
        )
        results = []
        for row in rows:
            try:
                content = json.loads(row[4]) if row[4] else {}
            except json.JSONDecodeError:
                content = {"raw": row[4]}
            results.append({
                "id": row[0],
                "agent_name": row[1],
                "river": row[2],
                "memory_type": row[3],
                "content": content,
                "created_at": str(row[5]),
            })
        return results
    except DatabaseError as e:
        logger.warning("[Memory] get_memory_window failed for %s: %s", agent_name, e)
        return []


def build_memory_context(agent_name: str) -> str:
    """Build a memory injection string for an agent's task context.

    Called at the START of every agent run, before anything else.
    Returns a string like:
    "Yesterday you [summary]. Today build on that. Do not repeat what was already done."
    """
    last = get_last_memory(agent_name, "daily_summary")
    if not last:
        return ""

    content = last["content"]
    if isinstance(content, dict):
        summary = content.get("summary", "")
    elif isinstance(content, str):
        summary = content
    else:
        summary = str(content)

    if not summary:
        return ""

    return (
        f"MEMORY FROM LAST RUN ({last['created_at']}): "
        f"{summary[:500]} "
        f"Today build on that. Do not repeat what was already done."
    )
