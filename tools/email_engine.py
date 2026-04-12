"""
tools/email_engine.py — Universal AI Prospect Parser & Email Engine
Parses raw CrewAI output from ANY sales agent into structured prospect dicts
with ready-to-send cold emails. Powers Tyler, Marcus, and Ryan Data.
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
import requests
import litellm
from config.runtime import resolve_llm_model_and_key


def _call_parser_llm(prompt: str, max_tokens: int = 3000) -> str:
    """Call the configured LLM via LiteLLM for parsing tasks. No separate API key needed."""
    model, api_key = resolve_llm_model_and_key()
    response = litellm.completion(
        model=model,
        api_key=api_key,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0,
    )
    return response.choices[0].message.content.strip()


def _strip_markdown_fences(text: str) -> str:
    text = re.sub(r"^```json\s*", "", text or "")
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_json_array(text: str):
    """Best-effort JSON array extraction from raw model output."""
    if not text:
        return None
    cleaned = _strip_markdown_fences(text)

    # Direct parse first
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass

    # Try first [...] block
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(cleaned[start:end + 1])
            if isinstance(parsed, list):
                return parsed
        except Exception:
            return None
    return None


def _extract_json_object(text: str):
    """Best-effort JSON object extraction from raw model output."""
    if not text:
        return None
    cleaned = _strip_markdown_fences(text)

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(cleaned[start:end + 1])
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None
    return None


def _needs_blog_expansion(piece: dict) -> bool:
    platform = (piece.get("platform") or "").strip().lower()
    content_type = (piece.get("content_type") or "").strip().lower()
    body = (piece.get("body") or "").strip()
    word_count = len(body.split())
    return platform == "blog" and content_type == "article" and word_count < 1200


def _expand_blog_piece(piece: dict, raw_output: str, agent_name: str) -> dict:
    """Expand a short blog brief into a full article body via direct LLM call.

    Uses higher temperature (0.8) and more tokens (7000) than standard parser calls
    to encourage longer, more creative, multi-section article generation.
    Writes body as plain text (not JSON) to maximize token budget for actual content.
    """
    import litellm
    from config.runtime import resolve_llm_model_and_key
    
    minimum_words = 1200
    title = piece.get("title", "")
    cta = piece.get("cta", "")
    funnel_stage = piece.get("funnel_stage", "awareness")

    base_prompt = f"""Write a complete, publication-ready blog article for a digital marketing agency serving small businesses in Dallas and North Texas.

TITLE: {title}
FUNNEL STAGE: {funnel_stage}
PRIMARY CTA: {cta}

CONTEXT (from agent research):
{raw_output[:3000]}

REQUIREMENTS:
- Minimum 1600 words. Aim for 1800-2400 or longer.
- Use markdown headers (## for H2, ### for H3).
- Structure: Intro (no header) → 6-7 substantial main sections → FAQ (## FAQ, 4+ Q&A pairs) → CTA section.
- Each section needs: detailed explanation, concrete examples, local references, step-by-step guidance.
- Include stats, customer quotes, common objections, and your differentiation.
- Local references: Dallas, Aubrey, Frisco, Prosper (naturally integrated).
- Weave in links naturally: "local SEO services in Aubrey", "website design for small businesses", "book a strategy session", "Google Ads management".
- Close with strong CTA: https://calendly.com/calling-michael/strategy-session
- Write ONLY the article body. No JSON, no metadata.

Article body:"""

    retry_suffix = (
        "\n\n[SECOND ATTEMPT - MAKE IT LONGER]\n"
        "Your draft was too short. Expand every section significantly: more examples, more breakdown, more local case studies, "
        "longer FAQ (8+ Q&A pairs), more objection handling, more subsections with ### headers, more actionable tips. "
        "Write 2000+ words minimum this time."
    )

    try:
        model, api_key = resolve_llm_model_and_key()
        for attempt in range(2):
            prompt = base_prompt if attempt == 0 else base_prompt + retry_suffix
            response = litellm.completion(
                model=model,
                api_key=api_key,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=7000,
                temperature=0.8,
            )
            body_text = response.choices[0].message.content.strip() if response.choices else ""
            body_words = len((body_text or "").split())
            
            if body_words >= minimum_words:
                result = dict(piece)
                result["body"] = body_text
                logging.info(f"[Parser] Blog expanded to {body_words} words for '{title}' (attempt {attempt+1})")
                return result
            logging.info(f"[Parser] Expansion attempt {attempt + 1} returned {body_words} words (need {minimum_words}), retrying")
        
        logging.warning(f"[Parser] Blog expansion exhausted retries, returned {len((body_text or '').split())} words for '{title}'")
    except Exception as e:
        logging.warning(f"[Parser] Blog expansion LLM failed for '{title}': {type(e).__name__}: {e}")
    
    return piece


def _expand_content_pieces(raw_output: str, pieces: list, agent_name: str) -> list:
    expanded = []
    for piece in pieces:
        if isinstance(piece, dict) and _needs_blog_expansion(piece):
            expanded.append(_expand_blog_piece(piece, raw_output, agent_name))
        else:
            expanded.append(piece)
    return expanded


def _heuristic_parse_prospects(raw_output: str) -> list:
    """Fallback parser for prospecting output when external parser LLM is unavailable."""
    prospects = []

    # Pattern for lines like:
    # 1. Business Name: X, Type: HVAC, City: Aubrey, Reason for targeting: Y
    inline_pattern = re.compile(
        r"Business\s*Name:\s*(?P<business_name>[^,\n]+),\s*"
        r"Type:\s*(?P<business_type>[^,\n]+),\s*"
        r"City:\s*(?P<city>[^,\n]+),\s*"
        r"Reason(?:\s*for\s*targeting)?:\s*(?P<reason>[^\n]+)",
        re.IGNORECASE,
    )

    inline_matches = list(inline_pattern.finditer(raw_output or ""))
    if inline_matches:
        lines = (raw_output or "").splitlines()
        for idx, m in enumerate(inline_matches):
            p = {
                "business_name": m.group("business_name").strip(),
                "city": m.group("city").strip(),
                "business_type": m.group("business_type").strip(),
                "reason": m.group("reason").strip(),
                "email_hook": "",
                "subject": "",
                "body": "",
                "follow_up_subject": "",
                "follow_up_body": "",
                "email": "",
            }

            # Search nearby lines for subject/body/follow-up
            start_line = 0
            for i, line in enumerate(lines):
                if m.group("business_name") in line:
                    start_line = i
                    break
            window = "\n".join(lines[start_line:start_line + 8])

            subject_match = re.search(r"Subject:\s*(.+)", window, re.IGNORECASE)
            body_match = re.search(r"Body:\s*(.+)", window, re.IGNORECASE)
            follow_match = re.search(
                r"Follow-up\s*angle\s*for\s*touch\s*2:\s*(.+)",
                window,
                re.IGNORECASE,
            )

            if subject_match:
                p["subject"] = subject_match.group(1).strip()
            if body_match:
                p["body"] = body_match.group(1).strip()
            if follow_match:
                p["follow_up_body"] = follow_match.group(1).strip()

            prospects.append(p)
    current = None

    key_map = {
        "business name": "business_name",
        "city": "city",
        "business type": "business_type",
        "reason": "reason",
        "email hook": "email_hook",
        "subject": "subject",
        "body": "body",
        "follow_up_subject": "follow_up_subject",
        "follow-up subject": "follow_up_subject",
        "follow_up_body": "follow_up_body",
        "follow-up body": "follow_up_body",
        "email": "email",
    }

    for raw_line in (raw_output or "").splitlines():
        line = raw_line.strip().lstrip("-*0123456789. ").strip()
        if not line or ":" not in line:
            continue

        left, right = line.split(":", 1)
        key = left.strip().lower()
        val = right.strip().strip("*")
        mapped = key_map.get(key)
        if not mapped:
            continue

        if mapped == "business_name":
            if current and current.get("business_name"):
                prospects.append(current)
            current = {
                "business_name": val,
                "city": "",
                "business_type": "",
                "reason": "",
                "email_hook": "",
                "subject": "",
                "body": "",
                "follow_up_subject": "",
                "follow_up_body": "",
                "email": "",
            }
            continue

        if current is None:
            continue
        current[mapped] = val

    if current and current.get("business_name"):
        prospects.append(current)

    # Fill defaults for sendable cold email fields
    for p in prospects:
        bname = p.get("business_name", "there")
        reason = p.get("reason", "")
        if not p.get("subject"):
            p["subject"] = "quick thought"
        if not p.get("body"):
            p["body"] = (
                f"Hi {bname},\n\n"
                f"Noticed {reason or 'a potential growth opportunity in your local market'}. "
                "Worth a quick look?\n\n"
                "If you'd rather not hear from us, just reply 'stop' and we'll remove you immediately."
            )
        if not p.get("email_hook"):
            p["email_hook"] = reason or "Potential growth opportunity identified."
        if not p.get("follow_up_subject"):
            p["follow_up_subject"] = "following up"
        if not p.get("follow_up_body"):
            p["follow_up_body"] = (
                f"Circling back on {reason or 'the opportunity I mentioned earlier'}. "
                "Happy to share details if useful."
            )

    return prospects


def _heuristic_parse_content(raw_output: str) -> list:
    """Fallback content parser for Groq-only deployments."""
    text = (raw_output or "").strip()
    if len(text) < 30:
        return []

    first_line = text.splitlines()[0][:120].strip() or "Daily content draft"
    return [
        {
            "platform": "linkedin",
            "content_type": "post",
            "title": first_line,
            "body": text[:3000],
            "hashtags": "",
            "cta": "Reply if you want this adapted for your business.",
            "funnel_stage": "awareness",
        }
    ]


# ── Agent-specific parsing prompts ───────────────────────────────────────────

PARSE_PROMPTS = {
    "tyler": {
        "keys": """Each object must have exactly these keys:
- business_name (string, full business name)
- business_type (string, e.g. "HVAC", "Plumbing", "Dental", "Roofing", "Personal Injury Law")
- city (string, city in DFW 380 Corridor)
- contact_name (string, owner or decision-maker first and last name — REQUIRED)
- email (string, direct contact email address — REQUIRED)
- phone (string, business phone number if found, otherwise empty string)
- website (string, business website URL if found, otherwise empty string)
- verified_fact (string, one specific verifiable fact from web research — NOT generic marketing copy)
- trigger_event (string, the specific trigger event making now the right time to reach out)
- competitive_insight (string, what their closest competitor does better)
- reason (string, 2-3 sentences on why this business needs an AI receptionist)""",
        "context": "This is from a research-focused SDR finding DFW 380 Corridor service businesses (HVAC, plumbing, roofing, dental, PI law) that need an AI receptionist. Tyler does NOT write emails — he delivers research intelligence. GHL workflows handle email delivery.",
    },
    "marcus": {
        "keys": """Each object must have exactly these keys:
- business_name (string, full business name)
- business_type (string, e.g. "med spa", "personal injury law", "real estate", "custom home building")
- vertical (string, one of: "med-spa", "pi-law", "real-estate", "home-builder")
- city (string, city in Texas)
- contact_name (string, owner or decision-maker first and last name — REQUIRED)
- email (string, direct contact email address — REQUIRED)
- phone (string, business phone number if found, otherwise empty string)
- website (string, business website URL if found, otherwise empty string)
- verified_fact (string, one specific verifiable fact from web research — NOT generic marketing copy)
- trigger_event (string, the specific trigger event making now the right time to reach out)
- competitive_insight (string, what their local competitor does digitally that they don't)
- reason (string, 2-3 sentences on why this business needs digital marketing help)""",
        "context": "This is from a research-focused SDR finding Texas businesses in 4 verticals (med spas, PI law, real estate, custom home builders) that need digital marketing services. Marcus does NOT write emails — he delivers research intelligence. The emails are pre-built in Attio sequences.",
    },
    "ryan_data": {
        "keys": """Each object must have exactly these keys:
- business_name (string, full dealership name)
- business_type (string, e.g. "Ford Dealership", "Independent Used", "Toyota Dealership")
- city (string, city in DFW area)
- contact_name (string, GM, BDC manager, or owner first and last name — REQUIRED)
- email (string, direct contact email address — REQUIRED)
- phone (string, dealership phone number if found, otherwise empty string)
- website (string, dealership website URL if found, otherwise empty string)
- group_affiliation (string, dealership group if known, otherwise empty string)
- verified_fact (string, one specific verifiable fact from web research — NOT generic marketing copy)
- trigger_event (string, the specific trigger event making now the right time to reach out)
- competitive_insight (string, what their closest competing dealer does better digitally)
- reason (string, 2-3 sentences on why this dealership needs an AI readiness assessment)""",
        "context": "This is from a research-focused SDR finding DFW car dealerships that need AI-powered operations. Ryan Data does NOT write emails — he delivers research intelligence. Instantly campaigns handle email delivery.",
    },
}


def parse_prospects(raw_output: str, agent_name: str = "tyler") -> list:
    """
    Universal prospect parser. Takes any sales agent's raw CrewAI output
    and returns structured prospect dicts with ready-to-send emails.

    Uses the configured LLM (via LiteLLM) to extract structured data from free-form text.
    No separate ANTHROPIC_API_KEY needed — reuses LLM_API_KEY/LLM_MODEL from Railway.
    """
    if not raw_output or len(raw_output.strip()) < 50:
        logging.warning(f"[Parser] {agent_name} output too short to parse.")
        return []

    agent_config = PARSE_PROMPTS.get(agent_name, PARSE_PROMPTS["tyler"])

    prompt = f"""Extract the prospect list from this sales prospecting report.
Return ONLY a JSON array. No markdown, no explanation, just the raw JSON array.

Context: {agent_config['context']}

{agent_config['keys']}

If a cold email subject and body are present in the report, extract them exactly.
If they are NOT present, generate appropriate ones based on the prospect details and the agent's style.

For Tyler: DO NOT generate emails. Tyler is a research agent — extract only the structured prospect data (business_name, business_type, contact_name, verified_fact, trigger_event, etc). No subject lines or email bodies needed.

For Marcus: DO NOT generate emails. Marcus is a research agent — extract only the structured prospect data (business_name, vertical, contact_name, verified_fact, trigger_event, etc). No subject lines or email bodies needed.

For Ryan Data: DO NOT generate emails. Ryan Data is a research agent — extract only the structured prospect data (business_name, business_type, contact_name, verified_fact, trigger_event, group_affiliation, etc). No subject lines or email bodies needed.

For ALL agents: Include CAN-SPAM compliant unsubscribe language at the end of body:
"If you'd rather not hear from us, just reply 'stop' and we'll remove you immediately."

If any field is missing, use an empty string.

REPORT:
{raw_output[:8000]}

JSON array:"""

    try:
        content = _call_parser_llm(prompt, max_tokens=4000)
        prospects = _extract_json_array(content)
        if prospects is None:
            raise ValueError("No parseable JSON array in parser response")
        if not prospects:
            logging.warning(f"[Parser] Parsed 0 prospects from {agent_name}; using heuristic fallback.")
            return _heuristic_parse_prospects(raw_output)
        logging.info(f"[Parser] Extracted {len(prospects)} prospects from {agent_name}'s output.")
        return prospects

    except json.JSONDecodeError as e:
        logging.error(f"[Parser] JSON parse failed for {agent_name}: {e}")
        return _heuristic_parse_prospects(raw_output)
    except Exception as e:
        logging.error(f"[Parser] Prospect parsing failed for {agent_name}: {e}")
        return _heuristic_parse_prospects(raw_output)


def parse_retention_actions(raw_output: str, agent_name: str = "jennifer") -> list:
    """
    Parse retention agent output into actionable items:
    - Emails to send to at-risk clients
    - Upsell messages to send to expansion-ready clients
    - Check-in templates ready to fire

    Returns list of dicts with: client_type, action, subject, body, urgency
    """
    if not raw_output or len(raw_output.strip()) < 50:
        return []

    prompt = f"""Extract actionable retention items from this client success report.
Return ONLY a JSON array. No markdown, no explanation.

Each object must have:
- action_type (string: "retention_email", "upsell_outreach", "check_in", "save_sequence")
- target_description (string: who this is for, e.g. "clients on Starter plan showing low usage")
- subject (string: email subject line)
- body (string: email body text, ready to send)
- urgency (string: "high", "medium", "low")
- trigger (string: what signal prompted this action)

Extract templates and talking points. If the report mentions email templates, extract them.
If it only has talking points, convert them into sendable email templates.

REPORT:
{raw_output[:3000]}

JSON array:"""

    try:
        content = _call_parser_llm(prompt, max_tokens=2000)
        actions = _extract_json_array(content)
        if actions is None:
            raise ValueError("No parseable JSON array in parser response")
        logging.info(f"[Parser] Extracted {len(actions)} retention actions from {agent_name}.")
        return actions
    except Exception as e:
        logging.error(f"[Parser] Retention parsing failed for {agent_name}: {e}")
        return []


def parse_content_pieces(raw_output: str, agent_name: str = "zoe") -> list:
    """
    Parse content agent output into publishable content pieces.
    Returns list of dicts with: platform, content_type, title, body, hashtags, cta, status

    This closes the loop: content agents generate → this parses → ready to publish.
    """
    if not raw_output or len(raw_output.strip()) < 50:
        return []

    prompt = f"""Extract publishable content pieces from this content marketing report.
Return ONLY a JSON array. No markdown, no explanation.

Each object must have:
- platform (string: "linkedin", "twitter", "instagram", "blog", "email", "newsletter")
- content_type (string: "post", "article", "story", "thread", "newsletter_section")
- title (string: headline or hook)
- body (string: full content ready to publish — not a summary, the actual post/article text)
- hashtags (string: relevant hashtags if social, empty string if not)
- cta (string: call to action)
- funnel_stage (string: "awareness", "consideration", "conversion")

Rules:
- If the report includes a full article draft, preserve that full article body instead of compressing it.
- If a blog/article entry is only a brief or outline, flesh it out into a publishable draft.
- Keep concrete URLs from the source content.
- Do not invent placeholder links.

REPORT:
{raw_output[:7000]}

JSON array:"""

    try:
        content = _call_parser_llm(prompt, max_tokens=4000)
        pieces = _extract_json_array(content)
        if pieces is None:
            raise ValueError("No parseable JSON array in parser response")
        if not pieces:
            logging.warning(f"[Parser] Parsed 0 content pieces from {agent_name}; using heuristic fallback.")
            return _heuristic_parse_content(raw_output)
        pieces = _expand_content_pieces(raw_output, pieces, agent_name)
        logging.info(f"[Parser] Extracted {len(pieces)} content pieces from {agent_name}.")
        return pieces
    except Exception as e:
        logging.error(f"[Parser] Content parsing failed for {agent_name}: {e}")
        return _heuristic_parse_content(raw_output)
