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

def get_llm():
    """
    Returns an LLM instance via LiteLLM.
    Provider/model are configurable via environment variables to support low-cost routing.
    """
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
    )
