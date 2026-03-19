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
