# services/crew_runner.py
"""The one way to run a CrewAI crew in paperclip: kickoff_fresh(crew).

Why this exists (2026-10-21 cost incident):
  Our agents are module-level singletons. CrewAI reuses an agent's executor
  across kickoffs and appends every run's system + user prompt to
  executor.messages, which it never clears. So each run re-sent the full
  history of every earlier run since the last restart: Darrell's hourly call
  reached ~56K prompt tokens and paperclip's OpenRouter spend doubled in a
  month. Dropping the executor before each kickoff makes CrewAI build a fresh
  one, so every run starts from an empty history.

It also enforces a hard monthly LLM budget: when month-to-date spend in
llm_spend_ledger reaches LLM_MONTHLY_CAP_USD, scheduled crews are skipped
(fail closed) and the skip is returned as the run's text so it lands in logs.
"""

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DEFAULT_MONTHLY_CAP_USD = 50.0


def reset_agent_history(crew) -> None:
    """Drop each agent's executor so the next kickoff starts with no messages."""
    for agent in getattr(crew, "agents", None) or []:
        try:
            agent.agent_executor = None
        except Exception as e:  # never let hygiene break a run
            logger.warning("[crew_runner] could not reset executor for %s: %s",
                           getattr(agent, "role", agent), e)


def monthly_cap_usd() -> float:
    try:
        return float(os.getenv("LLM_MONTHLY_CAP_USD", DEFAULT_MONTHLY_CAP_USD))
    except ValueError:
        return DEFAULT_MONTHLY_CAP_USD


def month_to_date_spend_usd() -> float | None:
    """Sum of llm_spend_ledger cost since the 1st of this UTC month; None if unreadable."""
    try:
        from services.database import fetch_all
        start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        rows = fetch_all(
            "SELECT COALESCE(SUM(cost_usd),0) FROM llm_spend_ledger WHERE ts >= %s",
            (start,),
        )
        return float(rows[0][0]) if rows else 0.0
    except Exception as e:
        logger.warning("[crew_runner] month-to-date spend unreadable: %s", e)
        return None


def kickoff_fresh(crew, job: str = "crew"):
    """Run crew.kickoff() with clean agent history, unless the monthly cap is hit.

    If the ledger can't be read, the run proceeds; the OpenRouter key's own
    credit limit is the backstop.
    """
    cap = monthly_cap_usd()
    spent = month_to_date_spend_usd()
    if spent is not None and cap > 0 and spent >= cap:
        msg = (f"[budget] Skipped {job}: month-to-date LLM spend ${spent:.2f} "
               f"reached LLM_MONTHLY_CAP_USD ${cap:.2f}. Raise the cap or wait for the 1st.")
        logger.warning(msg)
        return msg
    reset_agent_history(crew)
    return crew.kickoff()
