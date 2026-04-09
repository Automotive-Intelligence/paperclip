# AVO — AI Business Operating System
# AXIOM — Master CEO Orchestration Agent
# Built live for Agent Empire Skool community
# Salesdroid — April 2026
# North Star: $15,000 MRR

"""AXIOM — The Master CEO.

Runs every night at 11:30 PM CST after all agents have finished.
Reads all agent outputs from the day. Reasons across all 5 rivers.
Generates follow-on task assignments (directives) for the next morning.

This is the intelligence layer that makes AVO more than a task runner.
"""

import json
import logging
from datetime import datetime, date, timezone
from typing import Any, Dict, List

from services.database import execute_query, fetch_all
from services.errors import DatabaseError
from core.memory import save_memory, get_last_memory
from core.notifier import notify_axiom_directive

logger = logging.getLogger(__name__)

# Intelligence chains — which agent outputs trigger which follow-on tasks
INTELLIGENCE_CHAINS = [
    {
        "watch_agent": "tyler",
        "watch_signal": "prospect",
        "threshold": 5,
        "target_agent": "jennifer",
        "river": "aiphoneguy",
        "directive_template": "Tyler logged {count} new prospects today. Prepare onboarding materials for anticipated new clients in these verticals: {details}",
        "priority": "medium",
    },
    {
        "watch_agent": "atlas",
        "watch_signal": "dealer brief",
        "threshold": 1,
        "target_agent": "ryan_data",
        "river": "autointelligence",
        "directive_template": "Atlas completed a dealer brief. Pull intelligence on this dealer before Chase contacts them: {details}",
        "priority": "high",
    },
    {
        "watch_agent": "zoe",
        "watch_signal": "content",
        "threshold": 1,
        "target_agent": "zoe",
        "river": "aiphoneguy",
        "directive_template": "New content produced. Flag for review before routing to publishing: {details}",
        "priority": "medium",
    },
    {
        "watch_agent": "marcus",
        "watch_signal": "hot",
        "threshold": 1,
        "target_agent": "carlos",
        "river": "callingdigital",
        "directive_template": "Marcus logged a hot prospect. Draft a personalized case study for this prospect's vertical: {details}",
        "priority": "high",
    },
    {
        "watch_agent": "debra",
        "watch_signal": "content",
        "threshold": 1,
        "target_agent": "wade_ae",
        "river": "agentempire",
        "directive_template": "Debra produced a content calendar. Check if any tools mentioned are sponsor targets: {details}",
        "priority": "medium",
    },
    {
        "watch_agent": "clint",
        "watch_signal": "vera",
        "threshold": 1,
        "target_agent": "sherry",
        "river": "customeradvocate",
        "directive_template": "Clint completed a VERA build milestone. Begin corresponding UI component: {details}",
        "priority": "high",
    },
]


def run_axiom() -> Dict[str, Any]:
    """Main Axiom CEO run — 11:30 PM CST daily.

    1. Read all agent logs from today
    2. Analyze for intelligence chain triggers
    3. Generate and persist directives
    4. Save own memory (directives_history)
    """
    logger.info("[AXIOM] === CEO ORCHESTRATION RUN START ===")

    try:
        # 1. Read today's agent logs
        today_logs = _fetch_todays_logs()
        logger.info("[AXIOM] Read %d agent logs from today", len(today_logs))

        # 2. Check for duplicate directives (don't issue same one twice in a week)
        recent_directives = _fetch_recent_directives(days=7)
        recent_targets = {(d["target_agent"], d["directive"][:100]) for d in recent_directives}

        # 3. Analyze and generate directives
        directives_issued = []
        for chain in INTELLIGENCE_CHAINS:
            matching_logs = [
                log for log in today_logs
                if log["agent_name"] == chain["watch_agent"]
                and chain["watch_signal"].lower() in (log.get("content", "") or "").lower()
            ]

            if len(matching_logs) >= chain["threshold"]:
                # Extract relevant details from the logs
                details = "; ".join(
                    (log.get("content", "") or "")[:150] for log in matching_logs[:3]
                )

                directive_text = chain["directive_template"].format(
                    count=len(matching_logs),
                    details=details[:300],
                )

                # Check for dedup
                dedup_key = (chain["target_agent"], directive_text[:100])
                if dedup_key in recent_targets:
                    logger.info(
                        "[AXIOM] Skipping duplicate directive for %s",
                        chain["target_agent"],
                    )
                    continue

                # Persist directive
                directive_id = _create_directive(
                    target_agent=chain["target_agent"],
                    river=chain["river"],
                    directive=directive_text,
                    priority=chain["priority"],
                    triggered_by=chain["watch_agent"],
                )

                if directive_id:
                    directives_issued.append({
                        "target": chain["target_agent"],
                        "directive": directive_text[:100],
                        "priority": chain["priority"],
                        "triggered_by": chain["watch_agent"],
                    })
                    notify_axiom_directive(
                        chain["target_agent"],
                        directive_text,
                        chain["watch_agent"],
                    )

        # 4. Save Axiom's own memory
        save_memory("axiom", "directives_history", {
            "date": str(date.today()),
            "logs_analyzed": len(today_logs),
            "directives_issued": len(directives_issued),
            "directives": directives_issued,
            "summary": f"Analyzed {len(today_logs)} logs, issued {len(directives_issued)} directives",
        }, river="ceo")

        report = {
            "status": "ok",
            "logs_analyzed": len(today_logs),
            "directives_issued": len(directives_issued),
            "directives": directives_issued,
        }

        logger.info(
            "[AXIOM] === CEO RUN COMPLETE === Logs: %d | Directives: %d",
            len(today_logs), len(directives_issued),
        )
        return report

    except Exception as e:
        logger.error("[AXIOM] CEO orchestration failed: %s", e)
        return {"status": "error", "error": str(e)}


def _fetch_todays_logs() -> List[Dict[str, Any]]:
    """Fetch all agent logs from today."""
    try:
        rows = fetch_all(
            "SELECT agent_name, log_type, content, created_at "
            "FROM agent_logs "
            "WHERE run_date = CURRENT_DATE "
            "ORDER BY created_at DESC"
        )
        return [
            {
                "agent_name": r[0],
                "log_type": r[1],
                "content": str(r[2] or "")[:1000],
                "created_at": str(r[3]),
            }
            for r in rows
        ]
    except DatabaseError as e:
        logger.warning("[AXIOM] Failed to fetch today's logs: %s", e)
        return []


def _fetch_recent_directives(days: int = 7) -> List[Dict[str, Any]]:
    """Fetch recent directives for dedup check."""
    try:
        rows = fetch_all(
            "SELECT target_agent, directive, priority, triggered_by, created_at "
            "FROM axiom_directives "
            "WHERE created_at >= NOW() - INTERVAL '%s days'",
            (days,),
        )
        return [
            {
                "target_agent": r[0],
                "directive": r[1],
                "priority": r[2],
                "triggered_by": r[3],
                "created_at": str(r[4]),
            }
            for r in rows
        ]
    except DatabaseError:
        return []


def _create_directive(
    target_agent: str,
    river: str,
    directive: str,
    priority: str,
    triggered_by: str,
) -> bool:
    """Persist a directive to the axiom_directives table."""
    try:
        execute_query(
            "INSERT INTO axiom_directives "
            "(target_agent, river, directive, priority, triggered_by, status) "
            "VALUES (%s, %s, %s, %s, %s, 'pending')",
            (target_agent, river, directive, priority, triggered_by),
        )
        logger.info(
            "[AXIOM] Directive → %s: %s (triggered by %s)",
            target_agent, directive[:80], triggered_by,
        )
        return True
    except DatabaseError as e:
        logger.warning("[AXIOM] Failed to create directive: %s", e)
        return False


def get_pending_directives(agent_name: str) -> List[Dict[str, Any]]:
    """Get pending Axiom directives for an agent (called at start of each run)."""
    try:
        rows = fetch_all(
            "SELECT id, target_agent, river, directive, priority, triggered_by, created_at "
            "FROM axiom_directives "
            "WHERE target_agent = %s AND status = 'pending' "
            "ORDER BY CASE priority "
            "  WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, "
            "created_at ASC",
            (agent_name,),
        )
        return [
            {
                "id": r[0],
                "target_agent": r[1],
                "river": r[2],
                "directive": r[3],
                "priority": r[4],
                "triggered_by": r[5],
                "created_at": str(r[6]),
            }
            for r in rows
        ]
    except DatabaseError:
        return []


def pick_up_directive(directive_id: int) -> bool:
    """Mark directive as picked up by the target agent."""
    try:
        execute_query(
            "UPDATE axiom_directives SET status = 'picked_up' WHERE id = %s",
            (directive_id,),
        )
        return True
    except DatabaseError:
        return False


def complete_directive(directive_id: int) -> bool:
    """Mark directive as completed."""
    try:
        execute_query(
            "UPDATE axiom_directives SET status = 'complete' WHERE id = %s",
            (directive_id,),
        )
        return True
    except DatabaseError:
        return False


def build_directive_context(agent_name: str) -> str:
    """Build directive injection string for an agent's task context.

    Called at the START of every agent run, after memory, before main task.
    """
    directives = get_pending_directives(agent_name)
    if not directives:
        return ""

    lines = [f"AXIOM CEO DIRECTIVES ({len(directives)}):"]
    for d in directives:
        lines.append(
            f"  [{d['priority'].upper()}] {d['directive'][:200]} "
            f"(triggered by {d['triggered_by']})"
        )
        pick_up_directive(d["id"])

    return " ".join(lines)


def get_axiom_dashboard_data() -> Dict[str, Any]:
    """Get data for the Axiom panel on Pit Wall."""
    try:
        # Last run
        last_memory = get_last_memory("axiom", "directives_history")

        # Directive counts
        pending_rows = fetch_all(
            "SELECT COUNT(*) FROM axiom_directives WHERE status = 'pending'"
        )
        picked_rows = fetch_all(
            "SELECT COUNT(*) FROM axiom_directives WHERE status = 'picked_up'"
        )
        complete_rows = fetch_all(
            "SELECT COUNT(*) FROM axiom_directives "
            "WHERE status = 'complete' AND created_at >= CURRENT_DATE"
        )
        recent_rows = fetch_all(
            "SELECT target_agent, directive, priority, triggered_by "
            "FROM axiom_directives ORDER BY created_at DESC LIMIT 1"
        )

        most_recent = None
        if recent_rows:
            r = recent_rows[0]
            most_recent = {
                "target": r[0],
                "directive": r[1][:100],
                "priority": r[2],
                "triggered_by": r[3],
            }

        return {
            "last_run": last_memory["created_at"] if last_memory else None,
            "last_run_summary": last_memory["content"].get("summary", "") if last_memory else "",
            "directives_issued_last_night": last_memory["content"].get("directives_issued", 0) if last_memory else 0,
            "directives_pending": pending_rows[0][0] if pending_rows else 0,
            "directives_picked_up": picked_rows[0][0] if picked_rows else 0,
            "directives_completed_today": complete_rows[0][0] if complete_rows else 0,
            "most_recent_directive": most_recent,
        }
    except Exception as e:
        logger.warning("[AXIOM] Dashboard data failed: %s", e)
        return {
            "last_run": None,
            "directives_pending": 0,
            "directives_completed_today": 0,
        }
