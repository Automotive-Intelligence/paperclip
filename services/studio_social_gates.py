"""services/studio_social_gates.py -- the three Studio gates, ported to the cloud.

Each gate is an INDEPENDENT review call (the checker is never the producer), just
like the laptop engine's independent-reviewer subagents:

  1. iris_review(png, cfg)   -- multimodal: LOOK at the image, hunt for reasons to
     REJECT (garbled/misspelled text, OEM badges, faces, off-palette, AI artifacts).
     A failed image DROPS its post (studio prompt STEP 4.1).
  2. scrutineer_review(posts) -- adversarial copy editor: hook, no fabricated stats,
     CTA, compliance, craft. Returns fixed copy.
  3. conversion_review(posts) -- direct-response: one CTA per post matched to the
     funnel stage, loss-framed hook, objection pre-answered.

sanitize_em_dashes() is a DETERMINISTIC final guard so the "no em-dashes" rule can
never ship broken because a model forgot.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

from services.studio_social_llm import LLMError, llm_json

logger = logging.getLogger(__name__)

_EMDASH_RE = re.compile(r"\s*[—–]\s*")  # em dash / en dash w/ surrounding space


def sanitize_em_dashes(text: str) -> str:
    """Replace any em/en dash with ', ' and collapse the double spaces it can
    create. Deterministic belt-and-suspenders on the hard 'no em-dashes' rule."""
    out = _EMDASH_RE.sub(", ", text)
    return re.sub(r" {2,}", " ", out).strip()


def sanitize_posts(posts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Apply the em-dash guard to every platform string of every post."""
    for post in posts:
        plats = post.get("platforms") or {}
        for p, text in list(plats.items()):
            if isinstance(text, str):
                plats[p] = sanitize_em_dashes(text)
    return posts


def iris_review(image_bytes: bytes, brand_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Independent visual gate for ONE image. Returns {"passed": bool, "reason": str}.
    Given ONLY the rendered PNG + the brand's visual standards (never the caption
    or any intent to pass), and told to hunt for reasons to REJECT."""
    system = (
        "You are IRIS, an independent visual reviewer. You are shown ONE rendered "
        "image for the brand '%s' and NOTHING about the caption or the intent. Your "
        "job is to hunt for reasons to REJECT it, not to approve it. "
        "Brand visual standard: %s. "
        "HARD FAILS (any one = FAIL): garbled, misspelled, or gibberish rendered text "
        "anywhere in the image; a real OEM logo or vehicle badge; a recognizable luxury "
        "OEM vehicle (Mercedes/Audi/BMW grille); an off-palette or off-brand look; a "
        "fabricated human face presented as a real person; obvious AI artifacts "
        "(warped hands, melted objects, nonsense UI). "
        "Return ONLY JSON: {\"passed\": true|false, \"reason\": \"specific reason\"}."
        % (brand_cfg.get("display_name", ""), brand_cfg.get("themes_note", ""))
    )
    try:
        out = llm_json(system, "Review this image. Return only the JSON verdict.",
                       images=[image_bytes], max_tokens=400)
    except LLMError as e:
        # A gate that cannot run must FAIL CLOSED (never pass an unreviewed image).
        logger.warning("[studio-social] iris review errored, failing closed: %s", e)
        return {"passed": False, "reason": f"iris review error: {e}"}
    return {"passed": bool(out.get("passed")), "reason": str(out.get("reason") or "")}


def _review_copy(posts: List[Dict[str, Any]], system: str, label: str) -> Dict[str, Any]:
    """Shared shape for the two copy gates: send the posts, get back fixed copy
    keyed by post key + platform, plus notes. On error the copy passes through
    unchanged (copy gates refine, they do not DROP posts)."""
    import json as _json
    user = ("Review and, where needed, rewrite this copy. Return ONLY JSON: "
            "{\"posts\": {\"<key>\": {\"<platform>\": \"fixed text\"}}, \"notes\": [\"...\"]}. "
            "Keep every key and platform present; return the original text if it needs no change.\n\n"
            + _json.dumps([{"key": p["key"], "platforms": p["platforms"]} for p in posts]))
    try:
        out = llm_json(system, user, max_tokens=6000)
    except LLMError as e:
        logger.warning("[studio-social] %s errored, passing copy through: %s", label, e)
        return {"posts": posts, "notes": [f"{label} error (passed through): {e}"]}
    fixed = out.get("posts") or {}
    for post in posts:
        pf = fixed.get(post["key"]) or {}
        for plat, text in list(post["platforms"].items()):
            new = pf.get(plat)
            if isinstance(new, str) and new.strip():
                post["platforms"][plat] = new.strip()
    return {"posts": posts, "notes": list(out.get("notes") or [])}


def scrutineer_review(posts: List[Dict[str, Any]], brand_cfg: Dict[str, Any]) -> Dict[str, Any]:
    system = (
        "You are the SCRUTINEER, an independent adversarial copy editor for %s. "
        "For each post check: a real hook, the operator test (would a busy owner care), "
        "NO fabricated or unsourced stats, exactly one clear CTA, platform fit, "
        "compliance (no income/earnings/outcome guarantees), and craft. NO em-dashes. "
        "Rewrite only what fails; keep the brand voice: %s."
        % (brand_cfg.get("display_name", ""), brand_cfg.get("voice", ""))
    )
    return _review_copy(posts, system, "scrutineer")


def conversion_review(posts: List[Dict[str, Any]], brand_cfg: Dict[str, Any]) -> Dict[str, Any]:
    system = (
        "You are the CONVERSION STRATEGIST (direct response) for %s. For each post: "
        "ensure ONE CTA matched to the funnel stage, a loss-framed / stakes-first hook, "
        "the top objection pre-answered, and friction removed. Lead with the stake, not "
        "a self-introduction. NO em-dashes. Keep the brand voice; rewrite only to lift "
        "response." % brand_cfg.get("display_name", "")
    )
    return _review_copy(posts, system, "conversion")
