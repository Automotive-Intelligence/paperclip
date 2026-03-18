import os
from crewai import LLM

def get_llm():
    """
    Returns a Claude 3.5 Sonnet LLM instance configured for cost-efficient agent execution.
    Uses Anthropic API directly (no litellm compatibility issues).
    """
    return LLM(
        model=os.environ.get("MODEL_ID", "claude-3-5-sonnet-20241022"),
        provider="anthropic",
        api_key=os.environ.get("ANTHROPIC_API_KEY"),
    )
