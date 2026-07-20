"""services/studio_social_generate.py -- produce one brand's week of social posts.

ONE LLM call per brand returns a structured batch (N posts, each with per-platform
copy + one image prompt). This is the cloud port of the laptop engine's "dispatch
per-brand IM-voice writers" step: same editorial output, a single observable call
instead of a subagent fan-out. Every hard guardrail from the Studio prompt lives
in the system prompt; the gates downstream (Iris / Scrutineer / Conversion) still
review the result independently.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from services.studio_social_llm import LLMError, llm_json

logger = logging.getLogger(__name__)

# Platform copy-length ceilings the writer must respect. X is the hard one (the
# publish path also enforces it t.co-aware, but keeping the writer inside the
# limit avoids wasted regen).
_PLATFORM_LIMITS = {"x": 280, "linkedin": 3000, "facebook": 2000, "instagram": 2200}


class GenerationError(Exception):
    pass


def _system_prompt(brand_cfg: Dict[str, Any], platforms: List[str]) -> str:
    plats = ", ".join(platforms)
    compliance = ""
    if brand_cfg.get("business_key") == "aiphoneguy":
        compliance = ("AIPG: show the buyer's world (vans, ladders, job sites, a ringing "
                      "phone), never a desk; no OEM/brand badges on vehicles. ")
    return (
        "You are THE STUDIO writing ONE brand's batch of social posts for next week. "
        f"Brand: {brand_cfg['display_name']}. Voice: {brand_cfg.get('voice','')}. "
        f"Editorial focus: {brand_cfg.get('themes_note','')}. "
        f"{compliance}"
        "Return ONLY a JSON object, no prose, no fence.\n\n"
        "HARD GUARDRAILS (violating any is a failure):\n"
        "- NO em-dashes anywhere. Use periods, commas, or colons.\n"
        "- NO fabricated stats. Every number must be real and first-party, else use none.\n"
        "- One clear CTA per post, matched to the funnel stage; lead with the stake, not a self-intro.\n"
        "- No Agent-Empire jargon (no '22 agents', '5 rivers', '3 live CRMs'); translate to the buyer's value.\n"
        "- Platform-native length: X <= 280 chars INCLUDING any link; LinkedIn 1-3 short paragraphs; "
        "Facebook conversational; Instagram caption with a hook first line.\n\n"
        "The JSON object must have EXACTLY this shape:\n"
        '{ "posts": [ {\n'
        '   "key": "p1",                      // stable id, p1..pN\n'
        '   "theme": "one-line topic",\n'
        '   "platforms": { %s },              // ONLY these platforms, each a ready-to-post string\n'
        '   "image_prompt": "a cinematic, on-brand scene with NO rendered text, NO logos/badges, NO faces"\n'
        " } ] }\n"
        "Every post MUST include every listed platform key with non-empty copy, and exactly one image_prompt."
        % ", ".join(f'"{p}": "..."' for p in platforms)
    )


def generate_posts(brand_cfg: Dict[str, Any], week_label: str, count: int) -> List[Dict[str, Any]]:
    """Return a list of post dicts for the brand. Raises GenerationError on a
    malformed result (missing platform copy, no image prompt, etc.)."""
    platforms = list(brand_cfg.get("platforms") or [])
    if not platforms:
        raise GenerationError(f"{brand_cfg.get('display_name')}: no platforms configured")
    user = (f"Write {count} distinct social posts for the week of {week_label}. "
            f"Each post targets these platforms: {', '.join(platforms)}. "
            "Vary the angle across the posts. Return only the JSON object.")
    try:
        obj = llm_json(_system_prompt(brand_cfg, platforms), user)
    except LLMError as e:
        raise GenerationError(f"LLM failed: {e}")

    posts = obj.get("posts")
    if not isinstance(posts, list) or not posts:
        raise GenerationError("no posts in generated batch")
    clean: List[Dict[str, Any]] = []
    for i, post in enumerate(posts):
        key = str(post.get("key") or f"p{i + 1}")
        plats = post.get("platforms")
        if not isinstance(plats, dict):
            raise GenerationError(f"post {key}: platforms missing")
        for p in platforms:
            text = plats.get(p)
            if not isinstance(text, str) or not text.strip():
                raise GenerationError(f"post {key}: empty copy for {p}")
        if not (post.get("image_prompt") or "").strip():
            raise GenerationError(f"post {key}: no image_prompt")
        clean.append({
            "key": key,
            "theme": str(post.get("theme") or "").strip(),
            "platforms": {p: plats[p].strip() for p in platforms},
            "image_prompt": post["image_prompt"].strip(),
        })
    return clean
