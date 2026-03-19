import os
import sys
from crewai import LLM

def get_llm():
    """
    Returns an LLM instance via LiteLLM.
    Provider/model are configurable via environment variables to support low-cost routing.
    """
    # Priority order keeps backward compatibility with existing Railway variables.
    model_name = os.getenv("LLM_MODEL") or os.getenv("GROQ_MODEL") or "groq/llama-3.1-8b-instant"
    api_key = os.getenv("LLM_API_KEY")

    # Auto-pick a key for common providers when LLM_API_KEY is not explicitly set.
    if not api_key:
        if model_name.startswith("groq/"):
            api_key = os.getenv("GROQ_API_KEY")
        elif model_name.startswith("openrouter/"):
            api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
        elif model_name.startswith("deepseek/"):
            api_key = os.getenv("DEEPSEEK_API_KEY")
        else:
            api_key = os.getenv("OPENAI_API_KEY")

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
