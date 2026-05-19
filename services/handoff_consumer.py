"""
services/handoff_consumer.py

The piece every CrewAI runner was missing.

PROBLEM IT FIXES
----------------
Before this module, the cockpit bridge created `agent_handoffs` rows correctly
but the agent runners never read them. `_avo_sched_sofia` always called
`run_sofia_content()` which is hard-coded for Calling Digital's daily self-
promotion. A flag for a client like Paper & Purpose would land in
agent_handoffs, get marked `pending`, never be claimed, and never produce
output. Handoffs sat at `picked_up` (or `pending`) for >24h with no work.

WHAT THIS DOES
--------------
- `claim_pending_handoffs(agent_name)` — atomically pulls pending handoffs for
  one agent and marks them `picked_up`. Uses `FOR UPDATE SKIP LOCKED` so
  concurrent scheduled + event-triggered runs cannot grab the same row.
- `complete_handoff(id)` / `fail_handoff(id, err)` — terminal state writes.
- `drain_handoffs_for_agent(agent_name)` — claims, executes each via the
  generic CrewAI flag-execution path, marks terminal. Called by
  `_avo_wrap_run` BEFORE the daily self-content run so client work always
  goes first.
- `get_stale_handoffs(minutes)` — handoffs stuck in `picked_up` past a
  threshold. Surfaced on `/handoffs/stale` for visibility.

DESIGN NOTES
------------
- Generic execution path: build a Task from the flag payload (target, what,
  why_now, by_when, posted_by, _routing.reasoning) and kick off a single-
  agent Crew. The agent's role/backstory already encodes its lane.
- Per-agent caps (default 3 handoffs/run) so one bad flag cannot blow up
  a 5-minute scheduler slot.
- Failures are isolated: one handoff crash does not block the others or
  the daily self-content run.
- Output captured via `persist_log(agent, "cockpit_handoff", ...)` for the
  same approval / audit surface other runs use.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional

from services.database import execute_query, fetch_all
from services.errors import DatabaseError

logger = logging.getLogger(__name__)


DEFAULT_DRAIN_LIMIT = 3
DEFAULT_STALE_MINUTES = 60


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

def _parse_payload(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8")
        except Exception:
            return {}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


def claim_pending_handoffs(agent_name: str, limit: int = DEFAULT_DRAIN_LIMIT) -> List[Dict[str, Any]]:
    """Atomically claim up to `limit` pending handoffs for this agent.

    Uses FOR UPDATE SKIP LOCKED inside an UPDATE...RETURNING so two callers
    cannot claim the same row. High-priority handoffs go first; ties broken
    by created_at ASC (oldest first).
    """
    sql = """
        UPDATE agent_handoffs
        SET status = 'picked_up', picked_up_at = NOW()
        WHERE id IN (
            SELECT id FROM agent_handoffs
            WHERE to_agent = %s AND status = 'pending'
            ORDER BY
                CASE priority
                    WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2
                    WHEN 'low' THEN 3
                    ELSE 4
                END,
                created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT %s
        )
        RETURNING id, from_agent, river, handoff_type, payload, priority, created_at
    """
    try:
        rows = fetch_all(sql, (agent_name, limit))
    except DatabaseError as e:
        logger.warning("[HandoffConsumer] claim failed for %s: %s", agent_name, e)
        return []

    claimed: List[Dict[str, Any]] = []
    for r in rows:
        claimed.append({
            "id": r[0],
            "from_agent": r[1],
            "river": r[2],
            "handoff_type": r[3],
            "payload": _parse_payload(r[4]),
            "priority": r[5],
            "created_at": r[6],
        })
    if claimed:
        logger.info(
            "[HandoffConsumer] claimed %d handoff(s) for %s: ids=%s",
            len(claimed), agent_name, [h["id"] for h in claimed],
        )
    return claimed


def complete_handoff(handoff_id: int, output_summary: str = "") -> None:
    try:
        execute_query(
            "UPDATE agent_handoffs SET status = 'complete', completed_at = NOW() WHERE id = %s",
            (handoff_id,),
        )
        logger.info(
            "[HandoffConsumer] handoff #%s complete. summary=%s",
            handoff_id, (output_summary or "")[:200],
        )
    except DatabaseError as e:
        logger.warning("[HandoffConsumer] complete failed for #%s: %s", handoff_id, e)


def fail_handoff(handoff_id: int, error: str) -> None:
    try:
        execute_query(
            "UPDATE agent_handoffs SET status = 'failed', completed_at = NOW() WHERE id = %s",
            (handoff_id,),
        )
        logger.warning("[HandoffConsumer] handoff #%s failed: %s", handoff_id, error[:200])
    except DatabaseError as e:
        logger.warning("[HandoffConsumer] fail update failed for #%s: %s", handoff_id, e)


def get_stale_handoffs(stale_minutes: int = DEFAULT_STALE_MINUTES) -> List[Dict[str, Any]]:
    """Return handoffs stuck in `picked_up` past the threshold. Useful for
    /handoffs/stale and for an out-of-band alert."""
    sql = """
        SELECT id, to_agent, river, handoff_type, priority,
               created_at, picked_up_at
        FROM agent_handoffs
        WHERE status = 'picked_up'
          AND picked_up_at < NOW() - (%s || ' minutes')::interval
        ORDER BY picked_up_at ASC
    """
    try:
        rows = fetch_all(sql, (str(stale_minutes),))
    except DatabaseError as e:
        logger.warning("[HandoffConsumer] stale query failed: %s", e)
        return []
    return [
        {
            "id": r[0],
            "to_agent": r[1],
            "river": r[2],
            "handoff_type": r[3],
            "priority": r[4],
            "created_at": str(r[5]) if r[5] else None,
            "picked_up_at": str(r[6]) if r[6] else None,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Agent object lookup (lazy import to avoid circular deps + slow startup)
# ---------------------------------------------------------------------------

_AGENT_IMPORT_MAP: Dict[str, str] = {
    # AI Phone Guy
    "alex":         "agents.aiphoneguy.alex:alex",
    "zoe":          "agents.aiphoneguy.zoe:zoe",
    "tyler":        "agents.aiphoneguy.tyler:tyler",
    "jennifer":     "agents.aiphoneguy.jennifer:jennifer",
    "randy":        "agents.aiphoneguy.randy:randy",
    "joshua":       "agents.aiphoneguy.joshua:joshua",
    # Calling Digital
    "dek":          "agents.callingdigital.dek:dek",
    "sofia":        "agents.callingdigital.sofia:sofia",
    "marcus":       "agents.callingdigital.marcus:marcus",
    "carlos":       "agents.callingdigital.carlos:carlos",
    # "nova" intentionally absent — Paperclip CD nova was removed from
    # routing 2026-05-19; "nova" namespace now reserved for Customer
    # Advocate's NOVA buyer agent.
    "brenda":       "agents.callingdigital.brenda:brenda",
    # Automotive Intelligence
    "michael_meta": "agents.autointelligence.michael_meta:michael_meta",
    "chase":        "agents.autointelligence.chase:chase",
    "ryan_data":    "agents.autointelligence.ryan_data:ryan_data",
    "phoenix":      "agents.autointelligence.phoenix:phoenix",
    "atlas":        "agents.autointelligence.atlas:atlas",
    "darrell":      "agents.autointelligence.darrell:darrell",
    # Agent Empire
    "wade":         "agents.agentempire.wade:wade",
    "debra":        "agents.agentempire.debra:debra",
    "tammy":        "agents.agentempire.tammy:tammy",
    "sterling":     "agents.agentempire.sterling:sterling",
    # CustomerAdvocate
    "clint":        "agents.customeradvocate.clint:clint",
    "sherry":       "agents.customeradvocate.sherry:sherry",
    # Master
    "axiom":        "agents.ceo.axiom:axiom",
}


def _resolve_agent(agent_name: str):
    """Lazy import the CrewAI Agent object by name. Returns None if the
    agent module doesn't expose one (e.g., bookd_pipeline is an orchestrator
    function, not a CrewAI Agent)."""
    spec = _AGENT_IMPORT_MAP.get(agent_name)
    if not spec:
        return None
    module_path, _, attr = spec.partition(":")
    try:
        mod = __import__(module_path, fromlist=[attr])
        return getattr(mod, attr, None)
    except Exception as e:
        logger.warning("[HandoffConsumer] could not import %s: %s", spec, e)
        return None


# ---------------------------------------------------------------------------
# Generic flag execution
# ---------------------------------------------------------------------------

def _build_flag_task_description(handoff: Dict[str, Any]) -> str:
    p = handoff["payload"]
    target      = p.get("target", "")
    what        = p.get("what", "")
    why_now     = p.get("why_now", "")
    by_when     = p.get("by_when", "")
    posted_by   = p.get("posted_by", "")
    posted      = p.get("posted", "")
    routing     = p.get("_routing", {}) or {}
    reasoning   = routing.get("reasoning", "")
    priority    = handoff.get("priority", "medium")

    return (
        f"You have received a flag from the AVO Cockpit Bridge. Execute it.\n\n"
        f"=== FLAG CONTEXT ===\n"
        f"Source chat (posted by): {posted_by}\n"
        f"Target chat on flag:     {target}\n"
        f"Posted at:               {posted}\n"
        f"Priority:                {priority}\n"
        f"Bridge routing reasoning: {reasoning}\n\n"
        f"=== WORK REQUEST ===\n"
        f"WHAT:     {what}\n"
        f"WHY NOW:  {why_now}\n"
        f"BY WHEN:  {by_when}\n\n"
        f"=== EXECUTION RULES ===\n"
        f"- Produce a complete, ready-to-ship deliverable matching the request.\n"
        f"- If the flag names a specific client (e.g., Paper & Purpose, Worden Welding, "
        f"Book'd, Panda Construction, Miriam, Garrett, Ryan Velazquez), use that client's "
        f"brand voice and constraints. Otherwise default to your own river's brand.\n"
        f"- If the deliverable requires inputs you do not have (login credentials, "
        f"assets, approvals), state explicitly what is missing and why, then produce "
        f"the maximum partial deliverable possible without those inputs.\n"
        f"- Do not generate placeholder URLs, fake metrics, or fabricated entities. "
        f"Empty is better than fake.\n"
    )


def execute_handoff_via_agent(handoff: Dict[str, Any]) -> str:
    """Build a Task from the handoff payload, kick off a single-agent Crew,
    return raw output. Caller is responsible for marking handoff terminal.
    """
    # Local imports keep this module importable in contexts that don't need
    # CrewAI (tests, /handoffs/stale endpoint, etc).
    from crewai import Crew, Process, Task

    # Lazy import persist_log to avoid pulling app.py at module-load.
    from app import persist_log  # type: ignore

    agent_name = None
    # handoff dict carries to_agent via the row but our claim doesn't include
    # it (we filter by it in WHERE). The caller knows the agent_name.
    raise NotImplementedError(
        "execute_handoff_via_agent must be called with agent_name; "
        "use drain_handoffs_for_agent instead."
    )


def _execute_one(agent_name: str, handoff: Dict[str, Any]) -> str:
    from crewai import Crew, Process, Task
    from app import persist_log  # type: ignore

    agent_obj = _resolve_agent(agent_name)
    if agent_obj is None:
        raise RuntimeError(
            f"no CrewAI Agent registered for {agent_name!r} — cannot execute handoff"
        )

    task = Task(
        description=_build_flag_task_description(handoff),
        expected_output=(
            "The complete deliverable requested in the flag, ready to ship. "
            "If inputs are missing, an explicit list of what is missing plus the "
            "maximum partial deliverable possible without them."
        ),
        agent=agent_obj,
    )
    crew = Crew(
        agents=[agent_obj],
        tasks=[task],
        process=Process.sequential,
        memory=False,
        verbose=False,
    )
    result = crew.kickoff()
    raw_output = str(result)
    persist_log(agent_name, "cockpit_handoff", raw_output)
    return raw_output


def drain_handoffs_for_agent(
    agent_name: str,
    limit: int = DEFAULT_DRAIN_LIMIT,
    executor: Optional[Callable[[str, Dict[str, Any]], str]] = None,
) -> Dict[str, Any]:
    """Claim and execute pending handoffs for one agent. Returns a stats dict.

    Each handoff is executed in its own try/except so one failure does not
    block the rest. The terminal state (complete | failed) is always written.

    `executor` lets tests inject a stub. Defaults to `_execute_one`.
    """
    run = executor or _execute_one
    claimed = claim_pending_handoffs(agent_name, limit=limit)
    completed = 0
    failed = 0
    outputs: List[Dict[str, Any]] = []

    for h in claimed:
        try:
            output = run(agent_name, h)
            complete_handoff(h["id"], output[:500] if isinstance(output, str) else "")
            completed += 1
            outputs.append({"handoff_id": h["id"], "status": "complete"})
        except Exception as e:
            logger.exception(
                "[HandoffConsumer] handoff #%s for %s raised", h["id"], agent_name,
            )
            fail_handoff(h["id"], f"{type(e).__name__}: {e}")
            failed += 1
            outputs.append({"handoff_id": h["id"], "status": "failed", "error": str(e)})

    return {
        "agent": agent_name,
        "claimed": len(claimed),
        "completed": completed,
        "failed": failed,
        "results": outputs,
    }
