"""
tools/prospect_parser.py - Parse Tyler's raw CrewAI output into structured prospect dicts.
Uses the configured LLM (via LiteLLM) - same model as all agents, no extra API key needed.
"""

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
import re
import logging
import json
import litellm
from config.runtime import resolve_llm_model_and_key


def _call_llm(prompt: str) -> str:
    model, api_key = resolve_llm_model_and_key()
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
