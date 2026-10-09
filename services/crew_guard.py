"""services/crew_guard.py -- fail loud when a crew "succeeds" with nothing.

crewai 1.10.1 has known paths that return empty or placeholder output and still
look like a successful run: tool calls dropped on litellm models when the model
also returns text (crewAI #4788, fixed 1.14.4), OpenRouter errors returned inside
an HTTP 200 (fixed 1.15.21), and the forced-final-answer path at max_iter. Each
of these used to be persisted as a real briefing.

checked_kickoff() runs the crew, logs token usage, and raises EmptyCrewOutput when
the output is blank or is crewai's own iteration/time-limit placeholder. Callers
already wrap kickoff in try/except + logging, so a bad run becomes a logged
failure instead of a stored success.
"""

import logging
import os

MIN_OUTPUT_CHARS = int(os.getenv("CREW_MIN_OUTPUT_CHARS", "40"))

# crewai's own placeholder strings when an agent gives up without an answer.
_PLACEHOLDER_PREFIXES = (
    "agent stopped due to iteration limit or time limit",
)


class EmptyCrewOutput(RuntimeError):
    """The crew finished but produced no usable output."""


def validate_output(agent_name: str, raw: str) -> str:
    text = (raw or "").strip()
    if len(text) < MIN_OUTPUT_CHARS:
        raise EmptyCrewOutput(
            f"{agent_name}: crew output too short ({len(text)} chars, "
            f"min {MIN_OUTPUT_CHARS}): {text[:80]!r}"
        )
    if text.lower().startswith(_PLACEHOLDER_PREFIXES):
        raise EmptyCrewOutput(f"{agent_name}: crew gave up: {text[:120]!r}")
    return raw


def checked_kickoff(crew, agent_name: str, inputs=None):
    """crew.kickoff() plus an output check and a token-usage log line.

    Returns the CrewOutput unchanged, so str(result) keeps working at call sites.
    """
    result = crew.kickoff(inputs=inputs) if inputs is not None else crew.kickoff()
    usage = getattr(result, "token_usage", None)
    if usage is not None:
        logging.info(
            "[crew_guard] %s tokens total=%s prompt=%s cached=%s completion=%s requests=%s",
            agent_name,
            getattr(usage, "total_tokens", None),
            getattr(usage, "prompt_tokens", None),
            getattr(usage, "cached_prompt_tokens", None),
            getattr(usage, "completion_tokens", None),
            getattr(usage, "successful_requests", None),
        )
    raw = getattr(result, "raw", None)
    validate_output(agent_name, raw if raw is not None else str(result))
    return result
