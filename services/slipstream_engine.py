"""services/slipstream_engine.py -- the Railway Slipstream engine orchestrator.

run_brand() produces ONE full-Slipstream post for a brand and opens a PR, or
HOLDS on any gate violation. Every stage is an importable module function so it
is testable in isolation and observable in railway logs. Fire it on demand via
POST /admin/run-slipstream/{brand}; schedule it MWF once proven.
"""
from __future__ import annotations

import base64
import logging
import os
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, Optional

import requests
import yaml

from services.slipstream_assemble import assemble_mdx
from services.slipstream_generate import generate_post
from services.slipstream_github import publish_post
from services.slipstream_images import generate_images

logger = logging.getLogger(__name__)

_CFG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "slipstream_brands.yaml")


@lru_cache(maxsize=1)
def _load_cfg() -> dict:
    with open(_CFG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _brand_cfg(brand_key: str) -> dict:
    cfg = (_load_cfg().get("brands") or {}).get(brand_key)
    if not cfg:
        raise ValueError(f"unknown brand '{brand_key}' (not in config/slipstream_brands.yaml)")
    return {**cfg, "brand_key": brand_key}


def _next_topic(cfg: dict, token: str) -> str:
    """Read the brand's queue via GitHub REST and return the first unchecked topic."""
    url = f"https://api.github.com/repos/{cfg['repo']}/contents/{cfg['queue_path']}"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}",
                                   "Accept": "application/vnd.github+json"}, timeout=30)
    if not r.ok:
        raise ValueError(f"cannot read queue {cfg['queue_path']}: {r.status_code}")
    text = base64.b64decode(r.json()["content"]).decode("utf-8")
    m = re.search(r"^- \[ \]\s*(.+)$", text, re.M)
    if not m:
        raise ValueError(f"no unchecked topic in {cfg['queue_path']}")
    return m.group(1).strip()


def _social_md(post: Dict[str, Any]) -> str:
    s = post.get("social") or {}
    return (f"# Social pack: {post['title']}\n\n## LinkedIn\n\n{s.get('linkedin','')}\n\n"
            f"## X\n\n{s.get('x','')}\n")


def _pr_body(post: Dict[str, Any], image_count: int) -> str:
    return (f"Automated Slipstream post (Railway engine).\n\n- slug: `{post['slug']}`\n"
            f"- images: {image_count} (hero + in-body)\n- gates: passed (validate_post)\n\n"
            "Vercel preview builds this PR = the build-gate. Review + merge to publish.")


def run_brand(
    brand_key: str,
    *,
    topic: Optional[str] = None,
    token: Optional[str] = None,
    date_str: Optional[str] = None,
) -> Dict[str, Any]:
    """Produce + publish one post (as a PR), or HOLD on any gate violation.
    Returns a structured receipt (never raises to the caller for expected holds)."""
    if token is None:
        token = os.getenv("SLIPSTREAM_GH_TOKEN", "").strip()
    if not token:
        return {"ok": False, "held": True, "violations": ["SLIPSTREAM_GH_TOKEN missing"],
                "error": "no publish token"}
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        cfg = _brand_cfg(brand_key)
        topic = topic or _next_topic(cfg, token)
        post = generate_post(cfg, topic)
        images = generate_images(post["image_prompts"], cfg["business_key"])
        mdx, violations = assemble_mdx(post, date_str)
    except Exception as e:
        logger.exception("[slipstream] %s produce failed", brand_key)
        return {"ok": False, "held": True, "violations": [f"{type(e).__name__}: {e}"],
                "error": "produce failed"}

    if violations:
        logger.warning("[slipstream] %s HELD on gate: %s", brand_key, violations)
        return {"ok": False, "held": True, "violations": violations, "slug": post.get("slug")}

    slug = post["slug"]
    files: Dict[str, Any] = {f"{cfg['blog_dir']}/{slug}.mdx": mdx,
                             f"{cfg['blog_dir']}/{slug}.social.md": _social_md(post)}
    for name, data in images.items():
        files[f"public/blog/{slug}-{name}.png"] = data

    branch = f"slipstream/{slug}-{date_str}"
    try:
        pr_url = publish_post(cfg["repo"], branch, files, f"content: {post['title']}",
                              _pr_body(post, len(images)), token)
    except Exception as e:
        logger.exception("[slipstream] %s publish failed", brand_key)
        return {"ok": False, "held": False, "violations": [], "slug": slug,
                "error": f"publish failed: {type(e).__name__}: {e}"}

    logger.info("[slipstream] %s PUBLISHED PR %s (%d images)", brand_key, pr_url, len(images))
    return {"ok": True, "pr_url": pr_url, "slug": slug, "image_count": len(images), "violations": []}
