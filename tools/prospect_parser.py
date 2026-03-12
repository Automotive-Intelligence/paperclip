"""
tools/prospect_parser.py â Parse Tyler's raw CrewAI output into structured prospect dicts.
Uses Claude to extract the 5 prospects from whatever format Tyler writes them in.
"""

import os
import re
import logging
import json
import requests


def parse_tyler_prospects(raw_output: str) -> list:
    """
    Takes Tyler's raw CrewAI output string and returns a list of structured prospect dicts.
    Uses a lightweight Claude call to parse the unstructured text reliably.

    Returns list of dicts with keys:
        business_name, city, business_type, reason, sms_hook
    """
    if not raw_output or len(raw_output.strip()) < 50:
        logging.warning("[Parser] Tyler output too short to parse.")
        return []

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        logging.error("[Parser] ANTHROPIC_API_KEY not set â cannot parse prospects.")
        return []

    prompt = f"""Extract the prospect list from this sales prospecting report.
Return ONLY a JSON array. No markdown, no explanation, just the raw JSON array.

Each object must have exactly these keys:
- business_name (string)
- city (string) 
- business_type (string, e.g. "HVAC", "Plumbing", "Dental", "Roofing", "Law Firm")
- reason (string, why they are being targeted)
- sms_hook (string, the personalized SMS opening message)

If any field is missing, use an empty string.

REPORT:
{raw_output[:3000]}

JSON array:"""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 1000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        content = resp.json()["content"][0]["text"].strip()

        # Strip any accidental markdown fences
        content = re.sub(r"^```json\s*", "", content)
        content = re.sub(r"\s*```$", "", content)

        prospects = json.loads(content)
        logging.info(f"[Parser] Extracted {len(prospects)} prospects from Tyler's output.")
        return prospects

    except json.JSONDecodeError as e:
        logging.error(f"[Parser] JSON parse failed: {e}")
        return []
    except Exception as e:
        logging.error(f"[Parser] Prospect parsing failed: {e}")
        return []
