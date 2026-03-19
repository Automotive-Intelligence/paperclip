"""
tools/prospect_parser.py - Parse Tyler's raw CrewAI output into structured prospect dicts.
Uses the configured LLM (via LiteLLM) - same model as all agents, no extra API key needed.
"""

import os
import re
import logging
import json
import litellm


def _call_llm(prompt: str) -> str:
    model = os.getenv("LLM_MODEL") or os.getenv("GROQ_MODEL") or "groq/llama-3.1-8b-instant"
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        if model.startswith("groq/"): api_key = os.getenv("GROQ_API_KEY")
        elif model.startswith("openrouter/"): api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
        elif model.startswith("deepseek/"): api_key = os.getenv("DEEPSEEK_API_KEY")
        else: api_key = os.getenv("OPENAI_API_KEY")
    resp = litellm.completion(
        model=model, api_key=api_key,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1500, temperature=0,
    )
    return resp.choices[0].message.content.strip()


def parse_tyler_prospects(raw_output: str) -> list:
    """
    Takes Tyler's raw CrewAI output string and returns a list of structured prospect dicts.
    Uses the configured LLM to extract the 5 prospects.

    Returns list of dicts with keys:
        business_name, city, business_type, reason, email_hook
    """
    if not raw_output or len(raw_output.strip()) < 50:
        logging.warning("[Parser] Tyler output too short to parse.")
        return []

    prompt = (
        "Extract the prospect list from this sales prospecting report.\n"
        "Return ONLY a JSON array. No markdown, no explanation, just the raw JSON array.\n\n"
        "Each object must have exactly these keys:\n"
        "- business_name (string)\n"
        "- city (string)\n"
        "- business_type (string, e.g. \"HVAC\", \"Plumbing\", \"Dental\", \"Roofing\", \"Law Firm\")\n"
        "- reason (string, why they are being targeted)\n"
        "- email_hook (string, the personalized cold email subject line and opening line)\n\n"
        "If any field is missing, use an empty string.\n\n"
        f"REPORT:\n{raw_output[:3000]}\n\nJSON array:"
    )

    try:
        content = _call_llm(prompt)
        content = re.sub(r'^```json\s*', '', content)
        content = re.sub(r'\s*```$', '', content)
        prospects = json.loads(content)
        logging.info(f"[Parser] Extracted {len(prospects)} prospects from Tyler's output.")
        return prospects
    except json.JSONDecodeError as e:
        logging.error(f"[Parser] JSON parse failed: {e}")
        return []
    except Exception as e:
        logging.error(f"[Parser] Prospect parsing failed: {e}")
        return []
