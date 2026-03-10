import os
from crewai import LLM

def get_llm():
    return LLM(
        model=os.environ.get("MODEL_ID", "gpt-4o-mini"),
        api_key=os.environ.get("OPENAI_API_KEY"),
        base_url=os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    )
