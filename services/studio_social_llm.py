"""services/studio_social_llm.py -- the ONE LLM seam for the Studio social engine.

Routes through the Anthropic Messages API using ANTHROPIC_API_KEY (provisioned +
verified 2026-07-19). Anthropic is used rather than the blog engine's OpenRouter
route because (a) it is the only LLM key in the paperclip Doppler config, (b) it
is metered with credit (the blog engine's OpenRouter fallback existed because the
OLD raw key was out of credit -- no longer true), and (c) Claude is native
multimodal, which the Iris VISUAL gate needs to actually LOOK at the generated
PNGs and reject garbled text / OEM badges / faces.

One call, structured JSON out, parsed defensively (models append trailing prose).
The network call is a module seam (`_post_messages`) so every test mocks it and
never touches the wire, exactly like the slipstream generate tests.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

_API_URL = "https://api.anthropic.com/v1/messages"
_MODEL = os.getenv("STUDIO_SOCIAL_MODEL", "claude-sonnet-5")
_MAX_TOKENS = int(os.getenv("STUDIO_SOCIAL_MAX_TOKENS", "8000"))


class LLMError(Exception):
    pass


def _post_messages(body: Dict[str, Any], timeout: int = 180) -> Dict[str, Any]:
    """The single network seam. Raises LLMError on a non-2xx."""
    key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        raise LLMError("ANTHROPIC_API_KEY missing")
    r = requests.post(
        _API_URL,
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=body,
        timeout=timeout,
    )
    if not r.ok:
        raise LLMError(f"LLM {r.status_code}: {r.text[:300]}")
    return r.json()


def _content_text(resp: Dict[str, Any]) -> str:
    """Concatenate the text blocks of an Anthropic Messages response."""
    blocks = resp.get("content") or []
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()


def _first_json_object(text: str) -> Dict[str, Any]:
    """Parse the FIRST complete JSON object in `text`. Models often wrap it in a
    ```json fence or append trailing prose (json.loads -> 'Extra data')."""
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    start = text.find("{")
    if start < 0:
        raise LLMError("no JSON object in LLM response")
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
        return obj
    except json.JSONDecodeError as e:
        raise LLMError(f"model did not return valid JSON: {e}")


def _image_blocks(images: List[bytes]) -> List[Dict[str, Any]]:
    return [{
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(img).decode("ascii"),
        },
    } for img in images]


def llm_json(
    system: str,
    user: str,
    *,
    images: Optional[List[bytes]] = None,
    retries: int = 2,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """One structured-JSON call. `images` (PNG bytes) attaches them to the user
    turn for the multimodal Iris gate. Retries on transient/malformed output."""
    content: List[Dict[str, Any]] = [{"type": "text", "text": user}]
    if images:
        content = _image_blocks(images) + content  # images first, then the ask
    body = {
        "model": model or _MODEL,
        "max_tokens": max_tokens or _MAX_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": content}],
    }
    last: Exception = LLMError("no attempt")
    for attempt in range(retries + 1):
        try:
            resp = _post_messages(body)
            text = _content_text(resp)
            if not text:
                raise LLMError(f"empty LLM response (stop_reason={resp.get('stop_reason')})")
            return _first_json_object(text)
        except LLMError as e:
            last = e
            logger.warning("[studio-social] LLM attempt %d/%d failed: %s",
                           attempt + 1, retries + 1, e)
    raise last
