import os
from crewai import LLM

def get_llm():
    """
    Returns a Groq LLM instance (free preview tier, 500x faster than Claude).
    Cost-optimized for 24/7 agent execution: $0/month
    
    Hardcoded to Groq because crewai doesn't recognize it without explicit config.
    """
    groq_api_key = os.environ.get("GROQ_API_KEY")
    if not groq_api_key:
        raise ValueError("GROQ_API_KEY environment variable is required")
    
    return LLM(
        model="mixtral-8x7b-32768",
        provider="groq",
        api_key=groq_api_key,
    )
