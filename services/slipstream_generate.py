"""services/slipstream_generate.py -- deterministic content generation for the
Railway Slipstream engine.

ONE Anthropic Messages API call returns the post as structured JSON (frontmatter
fields + MDX body + image prompts + social drafts). No agentic loop: a single,
observable, testable call. The assembled MDX is checked by
services/slipstream_validate.validate_post before anything publishes.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict

_MODEL = "claude-sonnet-5"
_MAX_TOKENS = 8000

_REQUIRED_FIELDS = ("title", "description", "slug", "body_mdx", "image_prompts", "social")


class GenerationError(Exception):
    pass


def _client():
    from anthropic import Anthropic
    return Anthropic()


def _anthropic_json(system: str, user: str) -> Dict[str, Any]:
    """Call the model and parse a single JSON object from its text response.
    Tolerates a ```json ... ``` fence."""
    resp = _client().messages.create(
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = resp.content[0].text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise GenerationError(f"model did not return valid JSON: {e}")


def _system_prompt(brand_cfg: Dict[str, Any]) -> str:
    money = ", ".join(brand_cfg.get("money_pages") or [])
    return (
        "You write ONE agency-standard, AEO-maximized blog post for a brand, and return "
        "ONLY a JSON object (no prose, no fence). Brand voice: "
        f"{brand_cfg.get('voice', 'restrained, diagnostic, anti-hype, operator-grounded')}.\n\n"
        "HARD RULES: no em-dashes anywhere; no fabricated metrics or unsourced industry numbers "
        "(cite real published sources or stay qualitative); 1200-1800 words.\n\n"
        "The JSON object must have EXACTLY these keys:\n"
        '- "title": string\n'
        '- "description": string (<=160 chars)\n'
        '- "slug": kebab-case string\n'
        '- "body_mdx": the MDX body (NO frontmatter). It MUST contain, in order: an <AnswerFirst>'
        " 2-4 sentence direct answer as the FIRST element; one <EntityDefinition term=\"...\">"
        " early; at least one <Callout> or <ConsoleDiagram steps=\"A | B | C\" /> (steps is a"
        " pipe-delimited STRING, never an array); one <PullQuote>; a scannable list or table;"
        " question-shaped ## H2 headings each opening with a 1-2 sentence direct answer; "
        f"2-3 internal money-page links ({money}); 1-2 real external authority links. "
        "Reference exactly 2-3 in-body images as <img src=\"/blog/{slug}-{name}.png\" alt=\"...\"/>"
        " where {name} matches an image_prompts entry.\n"
        '- "image_prompts": array of 3-4 objects {"name": str, "prompt": str}. The FIRST MUST be'
        " name \"hero\". Each prompt is a cinematic, diagrammatic scene with NO text, logos, or faces.\n"
        '- "social": {"linkedin": str, "x": str} voice-locked drafts, no em-dashes.\n'
    )


def generate_post(brand_cfg: Dict[str, Any], topic: str) -> Dict[str, Any]:
    """Generate one structured post for `topic`. Raises GenerationError on a
    malformed result. The MDX is assembled + gated downstream."""
    user = f"Write the post for this topic: {topic}\nReturn only the JSON object."
    post = _anthropic_json(_system_prompt(brand_cfg), user)

    for field in _REQUIRED_FIELDS:
        if field not in post:
            raise GenerationError(f"generated post missing field: {field}")
    prompts = post.get("image_prompts") or []
    if not any((p or {}).get("name") == "hero" for p in prompts):
        raise GenerationError("image_prompts has no 'hero' entry (zero-image = auto-HOLD)")
    if not isinstance(post.get("social"), dict) or not post["social"].get("x"):
        raise GenerationError("social drafts incomplete")
    return post
