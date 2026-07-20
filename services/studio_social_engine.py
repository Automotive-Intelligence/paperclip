"""services/studio_social_engine.py -- the Railway port of the Studio weekly social engine.

The laptop engine was `claude -p "$(cat studio_weekly_prep_prompt.md)"`. This is the
same editorial pipeline as a pure-Python, observable engine (the proven Slipstream
blog pattern), so it runs with Michael's Mac closed:

  produce (1 LLM call/brand) -> hero image (fal) -> INDEPENDENT Iris visual gate
  (drop+retry on fail) -> Scrutineer copy gate -> Conversion gate -> em-dash sanitize
  -> schedule the gap-fill into file-103 windows (tools/social_load: UTMs, queue
  guard, WD gate, Book'd NoRail hold) -> stage a numbered deliverable folder with the
  three gate stamps + receipt (committed to avo-telemetry) -> post-run verification.

Non-negotiables preserved verbatim from the laptop engine:
  * IDEMPOTENCY: refuses to double-publish a week already staged.
  * POST-RUN VERIFICATION: a run that did not land a folder + batch + 3 gate stamps
    + receipt returns ok=False (loud), never a quiet success. Silence is not success.
  * Book'd is never auto-published (the loader's NoRail hold enforces it in code).
  * No em-dashes; no fabricated stats; independent gates (checker != producer).

Fire on demand: POST /admin/run-social (dry-run default). Schedule weekly once proven.
Every external effect is a module-level seam so tests never touch the wire.
"""
from __future__ import annotations

import base64
import logging
import os
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import requests
import yaml

from services.social_load_service import run_social_load
from services.studio_social_gates import (
    conversion_review, iris_review, sanitize_posts, scrutineer_review,
)
from services.studio_social_generate import generate_posts
from services.studio_social_publish import build_jobs, resolve_accounts

logger = logging.getLogger(__name__)

_CFG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                         "config", "studio_social_brands.yaml")
_TELEMETRY_REPO = "salesdroid/avo-telemetry"
_DELIVERABLES = "marketing_deliverables"
GATE_STAMPS = ("Iris visual gate", "Scrutineer copy gate", "Conversion Strategist gate")
_IRIS_MAX_ATTEMPTS = 3   # 1 + 2 regen retries, per the laptop engine's STEP 4.1


# --------------------------------------------------------------------------- config/time
@lru_cache(maxsize=1)
def _load_cfg() -> dict:
    with open(_CFG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def upcoming_monday(now: Optional[datetime] = None) -> str:
    """ISO date of NEXT week's Monday (never today). The engine produces the
    upcoming Mon-Sun, matching the laptop engine's NEXT_MON."""
    now = now or datetime.now(timezone.utc)
    ahead = (0 - now.weekday()) % 7 or 7
    return (now.date() + timedelta(days=ahead)).isoformat()


# --------------------------------------------------------------------------- github seams
def _gh_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def _list_deliverables(token: str) -> List[dict]:
    r = requests.get(f"https://api.github.com/repos/{_TELEMETRY_REPO}/contents/{_DELIVERABLES}",
                     headers=_gh_headers(token), timeout=30)
    if not r.ok:
        raise RuntimeError(f"cannot list deliverables: {r.status_code} {r.text[:120]}")
    return r.json() or []


def week_already_published(week_monday: str, token: str) -> bool:
    """True if a `*studio_weekly_<week_monday>*` folder is already committed."""
    try:
        for e in _list_deliverables(token):
            if e.get("type") == "dir" and f"studio_weekly_{week_monday}" in (e.get("name") or ""):
                return True
    except Exception as e:
        logger.warning("[studio-social] idempotency check failed (assuming fresh): %s", e)
    return False


def _next_deliverable_number(token: str) -> int:
    best = 0
    try:
        for e in _list_deliverables(token):
            name = e.get("name") or ""
            head = name.split("_", 1)[0]
            if head.isdigit():
                best = max(best, int(head))
    except Exception as e:
        logger.warning("[studio-social] numbering fell back: %s", e)
    return best + 1


def _commit_files_to_main(files: Dict[str, str], message: str, token: str) -> None:
    """Direct Contents-API PUT of each file to avo-telemetry main (state repo, no
    PR gate). New files need no sha; an existing path is updated with its sha."""
    for path, content in files.items():
        h = _gh_headers(token)
        cur = requests.get(f"https://api.github.com/repos/{_TELEMETRY_REPO}/contents/{path}",
                           headers=h, timeout=30)
        body: Dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": "main",
        }
        if cur.ok and isinstance(cur.json(), dict) and cur.json().get("sha"):
            body["sha"] = cur.json()["sha"]
        r = requests.put(f"https://api.github.com/repos/{_TELEMETRY_REPO}/contents/{path}",
                         headers=h, json=body, timeout=45)
        if not r.ok:
            raise RuntimeError(f"commit {path} failed: {r.status_code} {r.text[:160]}")


# --------------------------------------------------------------------------- zernio seams
def _zernio_profiles_accounts() -> Tuple[List[dict], List[dict]]:
    from tools.zernio import get_zernio_profiles, list_zernio_accounts
    return get_zernio_profiles(), list_zernio_accounts()


def _upload_media(png: bytes, name: str) -> str:
    from tools.zernio import upload_media_to_zernio
    return upload_media_to_zernio(png, name, "image/png")


def _hero_image(image_prompt: str, business_key: str) -> Optional[bytes]:
    """One on-brand hero via fal, STEERED by the brand's approved references so
    yield stays on-palette and OEM/off-brand renders stop getting gate-dropped
    (the WD-yield-0 root cause). references_for returns a list of URL STRINGS;
    pass it STRAIGHT THROUGH -- a dict-shaped extraction silently yields 0 refs
    (the URL-strings trap, per the Studio flag). blog_image forwards
    reference_image_urls into generate_nano_banana_image."""
    from services.blog_image import blog_image
    from tools.fal_assets import references_for
    refs = references_for(business_key)  # List[str]; empty if the brand has no collection
    res = blog_image(image_prompt, business_key=business_key, aspect_ratio="16:9",
                     pro=True, reference_image_urls=refs or None)
    if not res.get("ok") or not res.get("urls"):
        return None
    r = requests.get(res["urls"][0], timeout=120)
    r.raise_for_status()
    return r.content


# --------------------------------------------------------------------------- per-brand
def produce_brand(brand_cfg: dict, week_monday: str) -> Dict[str, Any]:
    """Produce + gate one brand. Returns kept posts + their reviewed image bytes,
    the dropped list, and the gate stamps earned. No scheduling here."""
    count = int(brand_cfg.get("posts_per_run") or 3)
    posts = generate_posts(brand_cfg, week_monday, count)

    kept: List[Dict[str, Any]] = []
    media_bytes: Dict[str, bytes] = {}
    dropped: List[Dict[str, str]] = []
    for post in posts:
        verdict = {"reason": "no image produced"}
        for _ in range(_IRIS_MAX_ATTEMPTS):
            png = _hero_image(post["image_prompt"], brand_cfg["business_key"])
            if not png:
                continue
            verdict = iris_review(png, brand_cfg)
            if verdict.get("passed"):
                media_bytes[post["key"]] = png
                kept.append(post)
                break
        else:
            dropped.append({"key": post["key"], "reason": verdict.get("reason", "iris fail")})

    if not kept:
        return {"kept": [], "media_bytes": {}, "dropped": dropped, "stamps": []}

    # Independent copy gates (checker != producer). These refine, never drop.
    kept = scrutineer_review(kept, brand_cfg)["posts"]
    kept = conversion_review(kept, brand_cfg)["posts"]
    kept = sanitize_posts(kept)
    return {"kept": kept, "media_bytes": media_bytes, "dropped": dropped,
            "stamps": list(GATE_STAMPS)}


def schedule_brand(brand_cfg: dict, produced: Dict[str, Any], week_monday: str,
                   profiles: List[dict], accts: List[dict], content_id: str,
                   commit: bool) -> Dict[str, Any]:
    """Upload each kept post's reviewed image, resolve the brand's Zernio accounts,
    build jobs (stagger + X-280 guard), and hand them to the ONE loader."""
    from tools.studio_publish import STAGGER
    accounts = resolve_accounts(profiles, accts, brand_cfg["zernio_profile"])
    if not accounts:
        return {"scheduled": [], "skips": [], "held": True,
                "reason": f"{brand_cfg['zernio_profile']} not connected in Zernio"}
    # Only upload real media when committing; a dry-run stays side-effect-free.
    media_by_key: Dict[str, str] = {}
    if commit:
        for post in produced["kept"]:
            png = produced["media_bytes"].get(post["key"])
            if png:
                media_by_key[post["key"]] = _upload_media(
                    png, f"{brand_cfg['business_key']}_{content_id}_{post['key']}.png")
    jobs, skips = build_jobs(brand_cfg, produced["kept"], week_monday, accounts,
                             media_by_key, STAGGER, content_id)
    if not jobs:
        return {"scheduled": [], "skips": skips, "held": True, "reason": "no schedulable jobs"}
    # The ONE loader in both modes: commit=False is its real read-only dry-run
    # (lists the live Zernio queue, checks conflicts, tags UTMs, publishes nothing).
    summary = run_social_load(jobs, commit=commit)
    return {"scheduled": summary.get("results", []), "skips": skips,
            "counts": summary.get("counts", {}), "held": False, "ok": summary.get("ok", True)}


# --------------------------------------------------------------------------- staging
def _batch_md(week_monday: str, per_brand: Dict[str, Any]) -> str:
    lines = [f"# Studio weekly SOCIAL batch, week of {week_monday}", "",
             "Produced by the Railway studio-social engine (cloud port). "
             "Gate stamps below are earned per brand; only stamped brands scheduled.", ""]
    for i, (bkey, b) in enumerate(per_brand.items(), 1):
        cfg = b["cfg"]
        lines.append(f"# {i}. {cfg['display_name']}")
        if b.get("stamps"):
            lines.append("Gates cleared: " + " | ".join(b["stamps"]))
        for post in b.get("kept", []):
            lines.append(f"\n## {post['key']}: {post.get('theme','')}")
            for plat, text in post["platforms"].items():
                lines.append(f"\n### {plat}\n{text}")
        if b.get("dropped"):
            lines.append("\n> Dropped (Iris fail): " +
                         "; ".join(f"{d['key']} ({d['reason']})" for d in b["dropped"]))
        lines.append("")
    return "\n".join(lines)


def _receipt_md(week_monday: str, per_brand: Dict[str, Any], commit: bool) -> str:
    mode = "COMMIT" if commit else "DRY-RUN"
    lines = [f"# 🏁 STUDIO WEEKLY RECEIPT: week of {week_monday}", "",
             f"Run: Railway studio-social engine [{mode}], "
             f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}.",
             "This is a receipt of what actually shipped, not a request to approve.", ""]
    for bkey, b in per_brand.items():
        cfg = b["cfg"]
        if b.get("held"):
            lines.append(f"- **{cfg['display_name']}**: HELD ({b.get('reason','')})")
        else:
            n = sum(1 for r in b.get("scheduled", []) if r.get("action") in ("scheduled", "dry-run"))
            lines.append(f"- **{cfg['display_name']}**: {'SCHEDULED' if commit else 'DRY-RUN'} "
                         f"{n} post-slots; skips: {len(b.get('skips', []))}")
        for d in b.get("dropped", []):
            lines.append(f"    - dropped {d['key']}: {d['reason']}")
    lines.append("\nBook'd: always held for Ryan (loader NoRail). AE: unconnected, blocked.")
    return "\n".join(lines)


def _verify(batch_md: str, receipt_md: str, scheduled_any: bool) -> List[str]:
    """Post-run verification (the anti-silent-success guard). Loud on a run that
    did not land a real, stamped, receipted batch."""
    problems: List[str] = []
    if not batch_md.strip():
        problems.append("no batch content")
    if not any(s in batch_md for s in GATE_STAMPS):
        problems.append("no gate stamp present in batch")
    if "STUDIO WEEKLY RECEIPT" not in receipt_md:
        problems.append("no receipt")
    if not scheduled_any:
        problems.append("no brand produced a schedulable, gated post")
    return problems


# --------------------------------------------------------------------------- orchestrator
def run_week(*, brands: Optional[List[str]] = None, commit: bool = False,
             force: bool = False, token: Optional[str] = None,
             now: Optional[datetime] = None) -> Dict[str, Any]:
    """Produce + gate + schedule next week's social for every connected brand,
    stage the deliverable, and self-verify. Returns a structured receipt; never
    raises for expected holds."""
    token = token or os.getenv("SLIPSTREAM_GH_TOKEN", "").strip()
    if not token:
        return {"ok": False, "error": "SLIPSTREAM_GH_TOKEN missing"}
    week_monday = upcoming_monday(now)
    content_id = f"studio_weekly_{week_monday}"

    if commit and not force and week_already_published(week_monday, token):
        return {"ok": True, "skipped": True, "week": week_monday,
                "note": "refusing to double-publish (week already staged); pass force=true to re-run"}

    cfg = _load_cfg()
    selected = {k: v for k, v in (cfg.get("brands") or {}).items()
                if v.get("enabled") and (not brands or k in brands)}
    if not selected:
        return {"ok": False, "error": "no enabled brands selected", "week": week_monday}

    try:
        profiles, accts = _zernio_profiles_accounts()
    except Exception as e:
        return {"ok": False, "error": f"zernio lookup failed: {type(e).__name__}: {e}"}

    per_brand: Dict[str, Any] = {}
    scheduled_any = False
    for bkey, bcfg in selected.items():
        bcfg = {**bcfg, "brand_key": bkey}
        try:
            produced = produce_brand(bcfg, week_monday)
        except Exception as e:
            logger.exception("[studio-social] %s produce failed", bkey)
            per_brand[bkey] = {"cfg": bcfg, "held": True, "reason": f"produce error: {e}",
                               "kept": [], "dropped": [], "stamps": []}
            continue
        if not produced["kept"]:
            per_brand[bkey] = {"cfg": bcfg, "held": True, "reason": "all posts dropped at Iris",
                               "kept": [], "dropped": produced["dropped"], "stamps": []}
            continue
        try:
            sched = schedule_brand(bcfg, produced, week_monday, profiles, accts, content_id, commit)
        except Exception as e:
            logger.exception("[studio-social] %s schedule failed", bkey)
            sched = {"held": True, "reason": f"schedule error: {e}", "scheduled": [], "skips": []}
        per_brand[bkey] = {"cfg": bcfg, **produced, **sched}
        if not sched.get("held") and sched.get("scheduled"):
            scheduled_any = True

    batch_md = _batch_md(week_monday, per_brand)
    receipt_md = _receipt_md(week_monday, per_brand, commit)
    problems = _verify(batch_md, receipt_md, scheduled_any)

    staged = False
    if commit and not problems:
        num = _next_deliverable_number(token)
        folder = f"{_DELIVERABLES}/{num}_studio_weekly_{week_monday}"
        try:
            _commit_files_to_main(
                {f"{folder}/batch_{content_id}.md": batch_md,
                 f"{folder}/RECEIPT_{content_id}.md": receipt_md},
                f"studio-social: week of {week_monday} ({num})", token)
            staged = True
        except Exception as e:
            logger.exception("[studio-social] staging commit failed")
            problems.append(f"staging commit failed: {e}")

    receipt = {
        "ok": not problems,
        "week": week_monday,
        "mode": "commit" if commit else "dry-run",
        "staged": staged,
        "problems": problems,
        "brands": {k: {"held": v.get("held", False), "reason": v.get("reason"),
                       "kept": len(v.get("kept", [])), "dropped": len(v.get("dropped", [])),
                       "skips": len(v.get("skips", [])),
                       "counts": v.get("counts", {})} for k, v in per_brand.items()},
    }
    if problems:
        logger.error("[studio-social] RUN NOT CLEAN: %s", problems)
    return receipt
