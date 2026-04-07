"""
tools/ghost.py - Ghost CMS publishing connector for Paperclip content distribution.
"""

import base64
import hashlib
import hmac
import html
import json
import logging
import os
import re
import time

from typing import Optional

from services.errors import ServiceCallError
from services.http_client import request_with_retry
from tools.image_gen import generate_image, image_gen_ready


def _slugify(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text[:96].strip("-") or "daily-update"


def _to_html_paragraphs(text: str) -> str:
    blocks = [b.strip() for b in (text or "").split("\n\n") if b.strip()]
    if not blocks:
        return ""
    return "".join(f"<p>{html.escape(block).replace(chr(10), '<br>')}</p>" for block in blocks)


def _ghost_env_name(business_key: str, suffix: str) -> str:
    return f"{(business_key or '').strip().upper()}_{suffix}"


def _ghost_api_url(business_key: str) -> str:
    return (
        os.getenv(_ghost_env_name(business_key, "GHOST_API_URL"), "")
        or os.getenv("GHOST_API_URL", "")
    ).strip().rstrip("/")


def _ghost_admin_key(business_key: str) -> str:
    return (
        os.getenv(_ghost_env_name(business_key, "GHOST_ADMIN_API_KEY"), "")
        or os.getenv("GHOST_ADMIN_API_KEY", "")
    ).strip()


def ghost_publish_ready(business_key: str) -> bool:
    """Ghost publishing requires a site API URL and Ghost Admin API key."""
    return bool(_ghost_api_url(business_key) and _ghost_admin_key(business_key))


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _ghost_jwt(admin_key: str) -> str:
    try:
        key_id, secret_hex = admin_key.split(":", 1)
    except ValueError as exc:
        raise ValueError("Ghost Admin API key must be in '<id>:<secret>' format.") from exc

    header = {"alg": "HS256", "typ": "JWT", "kid": key_id}
    now = int(time.time())
    payload = {"iat": now, "exp": now + 300, "aud": "/admin/"}

    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()
    signature = hmac.new(bytes.fromhex(secret_hex), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url(signature)}"


def _ghost_headers(business_key: str) -> dict:
    key = _ghost_admin_key(business_key)
    return {
        "Authorization": f"Ghost {_ghost_jwt(key)}",
        "Content-Type": "application/json",
    }


def _ghost_request(
    business_key: str,
    operation: str,
    method: str,
    path: str,
    *,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
    timeout: int = 15,
) -> dict:
    base_url = _ghost_api_url(business_key)
    response = request_with_retry(
        provider="ghost",
        operation=operation,
        method=method,
        url=f"{base_url}{path}",
        headers=_ghost_headers(business_key),
        params=params,
        json_body=json_body,
        timeout=timeout,
        max_attempts=3,
        backoff_seconds=0.7,
    )
    if response.ok:
        return response.data or {}
    if response.error is None:
        raise RuntimeError(f"ghost.{operation} failed with unknown error")
    raise ServiceCallError(response.error)


def publish_content_to_ghost(content_item: dict) -> dict:
    """Publish a queued content item to Ghost Admin API using HTML source."""
    business_key = (content_item.get("business_key") or "").strip() or "callingdigital"
    if not ghost_publish_ready(business_key):
        raise ValueError(
            f"Ghost publishing is not configured for {business_key}. "
            f"Set {_ghost_env_name(business_key, 'GHOST_API_URL')} and "
            f"{_ghost_env_name(business_key, 'GHOST_ADMIN_API_KEY')} in Railway."
        )

    title = (content_item.get("title") or "Calling Digital Update").strip()
    body = (content_item.get("body") or "").strip()
    cta = (content_item.get("cta") or "").strip()
    slug = _slugify(title)
    hashtags_raw = (content_item.get("hashtags") or "").strip()
    html_body = _to_html_paragraphs(body)
    if cta:
        html_body += f"<p><strong>{html.escape(cta)}</strong></p>"

    tags = [
        {"name": t.lstrip("#")}
        for t in hashtags_raw.split()
        if t.startswith("#") and len(t) > 1
    ]

    # ── Feature image via FLUX Schnell (~$0.003/post) ──────────────────────
    feature_image_url = None
    if image_gen_ready():
        try:
            img_prompt = (
                f"Blog post hero image for: {title}. "
                f"Professional digital marketing visual, wide banner format, "
                f"no text overlay, clean and modern."
            )
            img_result = generate_image(
                prompt=img_prompt,
                business_key=business_key,
                platform="default",
                aspect_ratio="16:9",
            )
            feature_image_url = img_result["urls"][0] if img_result.get("urls") else None
            if feature_image_url:
                logging.info("[Ghost] Feature image generated for post: %s", slug)
        except Exception as img_err:
            logging.warning("[Ghost] Feature image generation skipped: %s", img_err)
    # ───────────────────────────────────────────────────────────────────────

    post_data = {
        "title": title,
        "slug": slug,
        "html": html_body,
        "status": "published",
        "excerpt": body[:300].strip(),
        "tags": tags,
    }
    if feature_image_url:
        post_data["feature_image"] = feature_image_url

    payload = {"posts": [post_data]}

    data = _ghost_request(
        business_key,
        "publish_post",
        "POST",
        "/ghost/api/admin/posts/?source=html",
        json_body=payload,
        timeout=20,
    )
    posts = data.get("posts", [])
    if not posts:
        raise RuntimeError("Ghost publish failed: no post returned.")

    post = posts[0]
    return {
        "status": "published",
        "slug": post.get("slug", slug),
        "url": post.get("url", ""),
        "external_id": post.get("id", ""),
        "provider": "ghost",
    }