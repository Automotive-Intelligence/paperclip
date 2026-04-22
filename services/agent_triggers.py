"""
services/agent_triggers.py

Event-driven agent execution layer for the AVO Cockpit bridge.

When the bridge creates a handoff, it can optionally trigger the target
agent's existing runner function immediately instead of waiting for the
APScheduler cron/interval to fire. Same runner function, same code path,
same side effects — just now, not at 11:30pm CST.

Design:
- Registry maps agent_name -> callable (the same function APScheduler
  invokes). app.py populates this at startup. No circular imports.
- Triggers fire in a daemon thread so the bridge tick is not blocked by
  a multi-minute CrewAI run.
- Per-agent threading.Lock prevents double-firing: if an agent is mid-run,
  a second trigger no-ops with a "busy" log line. Scheduled runs share the
  same lock so there is no race between cron and event trigger.
- Entire module is gated by BRIDGE_EVENT_DRIVEN=true. Default off. Flip
  via Railway env vars. When off, the bridge behaves exactly as v2:
  handoffs land in agent_handoffs and the next scheduled run picks them up.
"""

import logging
import os
import threading
import time
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)


# name -> runner callable. Populated by app.py via register_runner().
AGENT_RUNNERS: Dict[str, Callable[[], None]] = {}

# name -> lock. Keeps scheduled run and event trigger from stepping on each
# other, and prevents double-trigger.
_AGENT_LOCKS: Dict[str, threading.Lock] = {}

# Track last trigger attempt for observability.
_LAST_TRIGGER: Dict[str, Dict[str, object]] = {}


def is_enabled() -> bool:
    """Event-driven mode is opt-in."""
    return os.environ.get("BRIDGE_EVENT_DRIVEN", "").strip().lower() in ("1", "true", "yes", "on")


def register_runner(agent_name: str, runner: Callable[[], None]) -> None:
    """Called by app.py during scheduler setup to wire each agent's runner."""
    AGENT_RUNNERS[agent_name] = runner
    _AGENT_LOCKS.setdefault(agent_name, threading.Lock())
    logger.debug("[AgentTrigger] registered runner for %s", agent_name)


def get_agent_lock(agent_name: str) -> threading.Lock:
    """Expose the per-agent lock so scheduled runners can share it too."""
    return _AGENT_LOCKS.setdefault(agent_name, threading.Lock())


def registered_agents() -> list:
    return sorted(AGENT_RUNNERS.keys())


def trigger_agent_run(
    agent_name: str,
    *,
    origin: str = "cockpit_bridge",
    handoff_id: Optional[int] = None,
) -> Dict[str, object]:
    """Kick off the target agent's runner in a daemon thread.

    Returns a dict describing the outcome — it never raises. On every branch
    a line is logged with [AgentTrigger] so Railway logs tell the story.

    Outcomes:
      - disabled: BRIDGE_EVENT_DRIVEN not true; nothing happens
      - unknown_agent: no runner registered for this name
      - busy: agent is currently running (lock held); skipped
      - started: thread launched
    """
    result: Dict[str, object] = {
        "agent": agent_name,
        "origin": origin,
        "handoff_id": handoff_id,
    }

    if not is_enabled():
        result["outcome"] = "disabled"
        return result

    runner = AGENT_RUNNERS.get(agent_name)
    if runner is None:
        result["outcome"] = "unknown_agent"
        logger.warning(
            "[AgentTrigger] unknown_agent: no runner registered for %r (origin=%s handoff_id=%s registered=%d)",
            agent_name, origin, handoff_id, len(AGENT_RUNNERS),
        )
        return result

    lock = _AGENT_LOCKS.setdefault(agent_name, threading.Lock())
    # Non-blocking acquire: if the agent is already running, we skip rather
    # than queue. The scheduled run is the backstop; the event trigger is a
    # best-effort acceleration.
    if not lock.acquire(blocking=False):
        result["outcome"] = "busy"
        logger.info(
            "[AgentTrigger] busy: %s is already running, skipping event trigger (origin=%s handoff_id=%s)",
            agent_name, origin, handoff_id,
        )
        return result

    def _runner_thread():
        started_at = time.time()
        try:
            logger.info(
                "[AgentTrigger] started: agent=%s origin=%s handoff_id=%s",
                agent_name, origin, handoff_id,
            )
            runner()
            logger.info(
                "[AgentTrigger] completed: agent=%s origin=%s handoff_id=%s elapsed=%.1fs",
                agent_name, origin, handoff_id, time.time() - started_at,
            )
        except Exception as e:
            logger.exception(
                "[AgentTrigger] errored: agent=%s origin=%s handoff_id=%s elapsed=%.1fs err=%s",
                agent_name, origin, handoff_id, time.time() - started_at, e,
            )
        finally:
            try:
                lock.release()
            except RuntimeError:
                pass

    thread = threading.Thread(
        target=_runner_thread,
        name=f"agent-trigger-{agent_name}",
        daemon=True,
    )
    thread.start()

    _LAST_TRIGGER[agent_name] = {
        "started_at": time.time(),
        "origin": origin,
        "handoff_id": handoff_id,
    }

    result["outcome"] = "started"
    result["thread_name"] = thread.name
    logger.info(
        "[AgentTrigger] dispatched: agent=%s origin=%s handoff_id=%s thread=%s",
        agent_name, origin, handoff_id, thread.name,
    )
    return result


def trigger_status() -> Dict[str, object]:
    """Observability snapshot for the /bridge/status endpoint."""
    return {
        "event_driven": is_enabled(),
        "registered_agents": registered_agents(),
        "registered_count": len(AGENT_RUNNERS),
        "last_triggers": {
            name: {
                "started_at": info["started_at"],
                "origin": info["origin"],
                "handoff_id": info["handoff_id"],
            }
            for name, info in _LAST_TRIGGER.items()
        },
    }
