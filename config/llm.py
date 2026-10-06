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

import os
import sys
from crewai import LLM
from config.runtime import resolve_llm_model_and_key

# crewai 1.10.1 prompts for trace viewing on first run in a fresh filesystem, and
# every Railway deploy is one. Opt out unless explicitly turned on.
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")

# Per-call LLM timeout. Without one a hung provider call blocks a scheduler
# worker indefinitely.
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "120"))

# Spread into every Agent(...). crewai's default max_iter is 25 ReAct steps,
# which lets a looping agent burn tokens long after it stopped making progress.
AGENT_LIMITS = {
    "max_iter": int(os.getenv("AGENT_MAX_ITER", "10")),
    "max_retry_limit": int(os.getenv("AGENT_MAX_RETRY_LIMIT", "1")),
}

def _ensure_spend_tracking():
    """Attach the LiteLLM -> llm_spend_ledger callback (idempotent, non-fatal)."""
    try:
        from services.litellm_ledger_hook import register
        register()
    except Exception:
        pass


def get_llm():
    """
    Returns an LLM instance via LiteLLM.
    Provider/model are configurable via environment variables to support low-cost routing.
    """
    _ensure_spend_tracking()
    model_name, api_key = resolve_llm_model_and_key()

    # Don't crash at import time; surface warning in logs/dashboard.
    if not api_key:
        print("⚠️  WARNING: No API key found for configured LLM model", file=sys.stderr)
        api_key = "placeholder-key-set-in-railway-variables"

    return LLM(
        model=model_name,
        provider="litellm",
        api_key=api_key,
        max_tokens=4000,
        timeout=LLM_TIMEOUT_SECONDS,
    )


def get_llm_research():
    """Returns a more capable LLM for broad research tasks (Marcus, Ryan Data).

    Uses Gemini Flash via OpenRouter — cheaper than DeepSeek on input,
    1M context (vs 64K), better at tool use and open-ended reasoning.
    Falls back to the default LLM if OpenRouter key isn't set.
    """
    _ensure_spend_tracking()
    api_key = (os.getenv("OPENROUTER_API_KEY") or os.getenv("LLM_API_KEY") or "").strip()
    if not api_key:
        return get_llm()

    return LLM(
        model="openrouter/google/gemini-2.5-flash",
        provider="litellm",
        api_key=api_key,
        max_tokens=4000,
        timeout=LLM_TIMEOUT_SECONDS,
    )
