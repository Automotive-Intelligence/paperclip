import os
import sys
from crewai import LLM

def get_llm():
    """
    Returns a Groq LLM instance (free preview tier, 500x faster than Claude).
    Cost-optimized for 24/7 agent execution: $0/month
    """
    groq_api_key = os.environ.get("GROQ_API_KEY")
    
    # Don't crash at import time - let app start and show error on dashboard
    if not groq_api_key:
        print("⚠️  WARNING: GROQ_API_KEY not set in environment", file=sys.stderr)
        groq_api_key = "placeholder-key-set-in-railway-variables"
    
    return LLM(
        model="mixtral-8x7b-32768",
        provider="groq",
        api_key=groq_api_key,
    )
