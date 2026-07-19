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

from datetime import timedelta

from services.slipstream_assemble import assemble_mdx
from services.slipstream_generate import generate_post
from services.slipstream_github import merge_when_green, publish_post
from services.slipstream_images import generate_images


def _distribute_social(cfg: dict, post: dict, slug: str, live_url: str) -> dict:
    """Best-effort: schedule the social pack via the ONE loader. NEVER fails the
    post (Zernio limits / not-connected are non-fatal)."""
    try:
        from services.social_load_service import run_social_load
        social = post.get("social") or {}
        base = datetime.now(timezone.utc) + timedelta(days=1)
        jobs = []
        for i, (platform, text) in enumerate((("linkedin", social.get("linkedin")),
                                               ("x", social.get("x")))):
            if not text:
                continue
            when = (base + timedelta(days=i, hours=i * 4)).strftime("%Y-%m-%dT%H:%M:%S")
            jobs.append({
                "brand": cfg["business_key"], "platform": platform,
                "content": f"{text}\n\n{live_url}", "scheduled_for": when,
                "content_id": slug, "entry_point": "blog_engine",
                "media_urls": [f"{live_url.rsplit('/blog/', 1)[0]}/blog/{slug}-hero.png"],
            })
        if not jobs:
            return {"ok": False, "note": "no social drafts"}
        return run_social_load(jobs, commit=True)
    except Exception as e:
        logger.warning("[slipstream] social distribution failed (non-fatal): %s", e)
        return {"ok": False, "error": str(e)}

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
    qrepo = cfg.get("queue_repo", cfg["repo"])
    url = f"https://api.github.com/repos/{qrepo}/contents/{cfg['queue_path']}"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}",
                                   "Accept": "application/vnd.github+json"}, timeout=30)
    if not r.ok:
        raise ValueError(f"cannot read queue {cfg['queue_path']}: {r.status_code}")
    text = base64.b64decode(r.json()["content"]).decode("utf-8")
    m = re.search(r"^- \[ \]\s*(.+)$", text, re.M)
    if not m:
        raise ValueError(f"no unchecked topic in {cfg['queue_path']}")
    return m.group(1).strip()


def _checkoff_topic(cfg: dict, topic: str, live_url: str, token: str) -> bool:
    """Mark '- [ ] {topic}' as '- [x] {topic} -> {live_url}' in the queue so a
    scheduled run never republishes the same topic. Best-effort (returns False
    if the topic is not a queue line, e.g. an on-demand explicit topic)."""
    try:
        qrepo = cfg.get("queue_repo", cfg["repo"])
        url = f"https://api.github.com/repos/{qrepo}/contents/{cfg['queue_path']}"
        h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        r = requests.get(url, headers=h, timeout=30)
        if not r.ok:
            return False
        data = r.json()
        text = base64.b64decode(data["content"]).decode("utf-8")
        pattern = re.compile(r"^- \[ \]\s*" + re.escape(topic) + r".*$", re.M)
        if not pattern.search(text):
            return False
        new_text = pattern.sub(f"- [x] {topic} → {live_url}", text, count=1)
        requests.put(url, headers=h, timeout=30, json={
            "message": f"content-queue: mark shipped ({topic[:50]})",
            "content": base64.b64encode(new_text.encode("utf-8")).decode("ascii"),
            "sha": data["sha"],
        })
        return True
    except Exception as e:
        logger.warning("[slipstream] queue check-off failed (non-fatal): %s", e)
        return False


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
    auto_merge: bool = True,
) -> Dict[str, Any]:
    """Produce a post, open a PR, then (if auto_merge) merge it once the Vercel
    build is green and distribute social. HOLDS on any gate violation or a red
    build. Returns a structured receipt (never raises for expected holds)."""
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

    logger.info("[slipstream] %s PR opened %s (%d images)", brand_key, pr_url, len(images))
    result = {"ok": True, "pr_url": pr_url, "slug": slug, "image_count": len(images), "violations": []}

    if not auto_merge:
        return {**result, "published": False, "note": "PR opened (auto_merge off)"}

    # Auto-publish, gated on the Vercel preview build. A red build HOLDS the PR.
    m = merge_when_green(cfg["repo"], pr_url, token)
    result["published"] = m["merged"]
    if not m["merged"]:
        logger.warning("[slipstream] %s NOT merged: %s", brand_key, m.get("reason"))
        return {**result, "note": f"held for review: {m.get('reason')}"}

    domain = cfg.get("domain", "")
    live_url = f"https://{domain}/blog/{slug}" if domain else pr_url
    result["live_url"] = live_url
    result["topic_checked_off"] = _checkoff_topic(cfg, topic, live_url, token)
    result["social"] = _distribute_social(cfg, post, slug, live_url)
    logger.info("[slipstream] %s PUBLISHED LIVE %s", brand_key, live_url)
    return result
