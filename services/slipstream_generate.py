"""services/slipstream_generate.py -- deterministic content generation for the
Railway Slipstream engine.

ONE LLM call (via OpenRouter) returns the post as structured JSON (frontmatter
fields + MDX body + image prompts + social drafts). No agentic loop: a single,
observable, testable call. The assembled MDX is checked by
services/slipstream_validate.validate_post before anything publishes.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict

import logging

import requests

# Route through OpenRouter (same provider paperclip's agents use, and the one
# with credit) rather than the raw Anthropic API. Model is env-overridable.
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_MODEL = os.getenv("SLIPSTREAM_MODEL", "google/gemini-2.5-flash")
_MAX_TOKENS = 8000

_REQUIRED_FIELDS = ("title", "description", "slug", "body_mdx", "image_prompts", "social")
# ts_posts_array (Worship Digital): a structured block array instead of an MDX string.
_REQUIRED_FIELDS_TS = ("title", "description", "slug", "body", "image_prompts", "social")
# Formats whose body is a STRUCTURED BLOCK ARRAY rather than an MDX string. Blocks
# are renderer-agnostic: WD serializes them to TS, P&P renders them to Shopify
# article HTML. Same generation contract, same validate_blocks gate.
_BLOCK_FORMATS = ("ts_posts_array", "shopify_article", "tsx_post")


logger = logging.getLogger(__name__)


class GenerationError(Exception):
    pass


def _llm_json_once(system: str, user: str) -> Dict[str, Any]:
    """Call the LLM (OpenRouter, OpenAI-compatible) and parse a single JSON
    object from its text response. Tolerates a ```json ... ``` fence."""
    key = (os.getenv("OPENROUTER_API_KEY") or os.getenv("LLM_API_KEY") or "").strip()
    if not key:
        raise GenerationError("OPENROUTER_API_KEY missing")
    r = requests.post(
        _OPENROUTER_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": _MODEL,
            "max_tokens": _MAX_TOKENS,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        },
        timeout=180,
    )
    if not r.ok:
        raise GenerationError(f"LLM {r.status_code}: {r.text[:300]}")
    j = r.json()
    choice = (j.get("choices") or [{}])[0]
    text = ((choice.get("message") or {}).get("content") or "").strip()
    if not text:
        raise GenerationError(f"empty LLM response (finish_reason={choice.get('finish_reason')})")
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    # Parse the FIRST complete JSON object; models often append trailing prose
    # after it, which json.loads() rejects with "Extra data".
    start = text.find("{")
    if start < 0:
        raise GenerationError("no JSON object in LLM response")
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
        return obj
    except json.JSONDecodeError as e:
        raise GenerationError(f"model did not return valid JSON: {e}")


def _llm_json(system: str, user: str, *, retries: int = 2) -> Dict[str, Any]:
    """Retry the LLM call on transient errors / malformed JSON (models
    occasionally return finish_reason=error or a JSON syntax slip)."""
    last: Exception = GenerationError("no attempt")
    for attempt in range(retries + 1):
        try:
            return _llm_json_once(system, user)
        except GenerationError as e:
            last = e
            logger.warning("[slipstream] LLM attempt %d/%d failed: %s", attempt + 1, retries + 1, e)
    raise last


def _system_prompt(brand_cfg: Dict[str, Any]) -> str:
    money = ", ".join(brand_cfg.get("money_pages") or [])
    comps = brand_cfg.get("components") or ["AnswerFirst", "EntityDefinition", "Callout", "PullQuote"]
    comps_str = ", ".join(comps)
    # Per-component exact syntax for the props-carrying components. These MUST be
    # self-closing with STRING props: a JSON-object child or an array prop
    # (<ConsoleDiagram>{...}</ConsoleDiagram>, steps={[...]}, stats={[...]}) is a
    # bare-brace/array expression that crashes the MDX build.
    comp_rules = ""
    if "ConsoleDiagram" in comps:
        comp_rules += (
            "- ConsoleDiagram is SELF-CLOSING and takes a single PIPE-delimited STRING. Emit EXACTLY: "
            "<ConsoleDiagram steps=\"First step | Second step | Third step\" caption=\"optional caption\" />. "
            "NEVER give it children, a JSON object, or curly braces. Both "
            "<ConsoleDiagram>{...}</ConsoleDiagram> and steps={[...]} crash the build.\n"
        )
    if "StatRow" in comps:
        comp_rules += (
            "- StatRow is SELF-CLOSING with scalar STRING props and is ONLY for a real cited stat. Emit EXACTLY: "
            "<StatRow value=\"54%\" label=\"what it measures\" source=\"Publisher, Year\" href=\"https://...\" />. "
            "It requires a real source; never pass an array like stats={[...]}.\n"
        )
    return (
        "You write ONE agency-standard, AEO-maximized blog post for a brand, and return "
        "ONLY a JSON object (no prose, no fence). Brand voice: "
        f"{brand_cfg.get('voice', 'restrained, diagnostic, anti-hype, operator-grounded')}.\n\n"
        "HARD RULES: no em-dashes anywhere; no fabricated metrics or unsourced industry numbers "
        "(cite real published sources or stay qualitative); 1200-1800 words.\n\n"
        "MDX STRUCTURE RULES (a broken tag or expression crashes the build, obey exactly):\n"
        "- The FIRST characters of body_mdx MUST be a complete, CLOSED AnswerFirst: "
        "<AnswerFirst>your 2-4 sentence answer here</AnswerFirst>. Never empty or unclosed.\n"
        f"- Use ONLY these components (the ONLY ones this repo defines): {comps_str}. Using ANY component not in that list crashes the build.\n"
        f"{comp_rules}"
        "- Every paired component you open MUST be closed on the SAME line, no blank line inside: "
        "<PullQuote>text</PullQuote>, <Callout>text</Callout>, <EntityDefinition term=\"X\">definition</EntityDefinition>.\n"
        "- Do NOT put bare curly braces { } in prose text. MDX parses { } as JavaScript and a stray brace crashes "
        "the build. Write 'about 50 dollars', never a brace.\n"
        "- Section headings use EXACTLY two hashes: ## Question-shaped heading. Never ### or ####.\n\n"
        "The JSON object must have EXACTLY these keys:\n"
        '- "title": string\n'
        '- "description": string (<=160 chars)\n'
        '- "slug": kebab-case string\n'
        '- "body_mdx": the MDX body (NO frontmatter). It MUST contain, in order: an <AnswerFirst>'
        " 2-4 sentence direct answer as the FIRST element; one <EntityDefinition term=\"...\">"
        " early; at least one <Callout>text</Callout>; one <PullQuote>text</PullQuote>; a scannable list or table;"
        " question-shaped ## H2 headings each opening with a 1-2 sentence direct answer; "
        f"2-3 internal money-page links ({money}); 1-2 real external authority links. "
        "Reference exactly 2-3 in-body images as <img src=\"/blog/{slug}-{name}.png\" alt=\"...\"/>"
        " where {name} matches an image_prompts entry.\n"
        '- "image_prompts": array of 3-4 objects {"name": str, "prompt": str}. The FIRST MUST be'
        " name \"hero\". Each prompt is a cinematic, diagrammatic scene with NO text, logos, or faces.\n"
        '- "social": {"linkedin": str, "x": str} voice-locked drafts, no em-dashes.\n'
    )


def _system_prompt_ts(brand_cfg: Dict[str, Any]) -> str:
    """The block-format output contract (ts_posts_array, shopify_article): a
    STRUCTURED block array using WD's Block type names, not an MDX string. Same AEO
    + brand rules as the MDX path. Per-brand rules ride in via brand_cfg['voice']."""
    money = ", ".join(brand_cfg.get("money_pages") or [])
    return (
        "You write ONE agency-standard, AEO-maximized blog post for a brand, and return "
        "ONLY a JSON object (no prose, no fence). Brand voice: "
        f"{brand_cfg.get('voice', 'transparent, plain, owner-to-owner, anti-buzzword')}.\n\n"
        "HARD RULES: no em-dashes anywhere (use periods or commas); no fabricated metrics or "
        "unsourced industry numbers (only cite real published sources in a 'stat' or 'sources' "
        "block, otherwise stay qualitative); 1200-1800 words; never name DataMoon.\n\n"
        "OUTPUT: the post body is a STRUCTURED ARRAY of typed blocks (NOT markdown, NOT MDX). "
        "Each block is an object with a \"type\" and the fields for that type. Allowed blocks:\n"
        '- {"type":"answer","text":str}  the answer-first block, lifted verbatim by answer engines\n'
        '- {"type":"p","text":str}\n'
        '- {"type":"h2","text":str}  a question-shaped heading\n'
        '- {"type":"h3","text":str}\n'
        '- {"type":"ul","items":[str,...]}\n'
        '- {"type":"definition","term":str,"text":str}  one entity-clarity definition\n'
        '- {"type":"callout","title":str,"text":str}  a highlighted key insight\n'
        '- {"type":"quote","text":str}  a pull quote\n'
        '- {"type":"image","src":"/blog/{slug}-{name}.png","alt":str,"caption":str}  {name} MUST match an image_prompts name\n'
        '- {"type":"links","title":str,"items":[{"label":str,"href":str},...]}  internal money-page links\n'
        '- {"type":"table","headers":[str,...],"rows":[[str,...],...]}\n'
        '- {"type":"stat","value":str,"label":str,"source":str,"href":str}  ONLY with a real cited source\n'
        '- {"type":"sources","title":str,"items":[{"label":str,"href":str},...]}  real external authority links\n\n'
        "STRUCTURE RULES (obey exactly):\n"
        "- The FIRST block MUST be an 'answer' block (2-4 sentence direct answer). Exactly ONE answer block.\n"
        "- Include exactly one 'definition' block early, at least one 'callout', at least one 'quote'.\n"
        "- Section headings are 'h2' blocks phrased as questions, each followed by a 'p' that opens with a direct answer.\n"
        "- Include at least TWO 'image' blocks whose src is /blog/{slug}-{name}.png and whose {name} matches an image_prompts entry.\n"
        f"- Include one 'links' block with 2-3 internal money-page links ({money}).\n"
        "- Include one 'sources' block with 1-2 real external authority links.\n\n"
        "The JSON object must have EXACTLY these keys:\n"
        '- "title": string\n'
        '- "description": string (<=160 chars)\n'
        '- "slug": kebab-case string\n'
        '- "category": short string (e.g. "Working With an Agency", "Local SEO")\n'
        '- "heroAlt": string describing the hero image (no text/logos/faces)\n'
        '- "ogTitle": string\n'
        '- "faq": array of 3-4 {"q": str, "a": str} objects, each answer 2-4 sentences\n'
        '- "body": the array of typed blocks described above\n'
        '- "image_prompts": array of 3-4 objects {"name": str, "prompt": str}. The FIRST MUST be'
        " name \"hero\". Each prompt is a cinematic scene with NO text, logos, or faces.\n"
        '- "social": {"linkedin": str, "x": str} voice-locked drafts, no em-dashes.\n'
    )


def _generate_post_ts(brand_cfg: Dict[str, Any], topic: str) -> Dict[str, Any]:
    """Generate one post as a structured block array. Raises GenerationError on a
    malformed result. The Post is assembled + gated (validate_blocks) downstream."""
    user = f"Write the post for this topic: {topic}\nReturn only the JSON object."
    post = _llm_json(_system_prompt_ts(brand_cfg), user)

    for field in _REQUIRED_FIELDS_TS:
        if field not in post:
            raise GenerationError(f"generated post missing field: {field}")
    body = post.get("body")
    if not isinstance(body, list) or not body:
        raise GenerationError("body is not a non-empty block array")
    if (body[0] or {}).get("type") != "answer":
        raise GenerationError("first body block must be an 'answer' block (answer-first)")
    prompts = post.get("image_prompts") or []
    if not any((p or {}).get("name") == "hero" for p in prompts):
        raise GenerationError("image_prompts has no 'hero' entry (zero-image = auto-HOLD)")
    if not isinstance(post.get("social"), dict) or not post["social"].get("x"):
        raise GenerationError("social drafts incomplete")
    return post


def generate_post(brand_cfg: Dict[str, Any], topic: str) -> Dict[str, Any]:
    """Generate one structured post for `topic`. Raises GenerationError on a
    malformed result. Dispatches on the brand's output format: the default MDX path
    returns body_mdx; the ts_posts_array path (WD) returns a structured block array.
    Assembled + gated downstream."""
    if brand_cfg.get("format") in _BLOCK_FORMATS:
        return _generate_post_ts(brand_cfg, topic)

    user = f"Write the post for this topic: {topic}\nReturn only the JSON object."
    post = _llm_json(_system_prompt(brand_cfg), user)

    for field in _REQUIRED_FIELDS:
        if field not in post:
            raise GenerationError(f"generated post missing field: {field}")
    prompts = post.get("image_prompts") or []
    if not any((p or {}).get("name") == "hero" for p in prompts):
        raise GenerationError("image_prompts has no 'hero' entry (zero-image = auto-HOLD)")
    if not isinstance(post.get("social"), dict) or not post["social"].get("x"):
        raise GenerationError("social drafts incomplete")
    return post
