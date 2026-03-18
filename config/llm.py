import os
from crewai import LLM

def get_llm():
    """
    Returns a Groq LLM instance (free preview tier, 500x faster than Claude).
    Cost-optimized for 24/7 agent execution: $0/month
    """
    return LLM(
        model=os.environ.get("MODEL_ID", "mixtral-8x7b-32768"),
        provider="groq",
        api_key=os.environ.get("GROQ_API_KEY"),
    )
