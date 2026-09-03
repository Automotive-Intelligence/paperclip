"""Manual-posting outbox: paste-ready social packs instead of Zernio sends.

Michael took Worship Digital, Book'd and Agent Empire OFF automated posting on
2026-09-03 because Zernio costs real money. Those brands still get produced and
gated exactly as before; the only thing that changes is the last mile. Instead
of calling the loader, the engines write a pack here and he posts by hand.

Export mode makes ZERO Zernio calls for the brand: no account resolution, no
media upload, no schedule. That is the point. A pack that quietly still uploaded
its images would keep billing while looking like a saving.

Delivery is the repo (avo-telemetry/social_outbox/), which the laptop mirrors
into ~/Documents/Social Outbox via tools/social_outbox_pull.sh. The repo is the
durable copy; the folder is the one Michael actually opens.
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

TELEMETRY_REPO = "salesdroid/avo-telemetry"
OUTBOX_ROOT = "social_outbox"


def _headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def _put(path: str, raw: bytes, message: str, token: str) -> None:
    """Contents-API PUT of BYTES. The engine's own _commit_files_to_main encodes
    str only, which cannot carry a PNG; a pack without its image is not postable."""
    h = _headers(token)
    cur = requests.get(f"https://api.github.com/repos/{TELEMETRY_REPO}/contents/{path}",
                       headers=h, timeout=30)
    body: Dict[str, Any] = {"message": message, "branch": "main",
                            "content": base64.b64encode(raw).decode("ascii")}
    if cur.ok and isinstance(cur.json(), dict) and cur.json().get("sha"):
        body["sha"] = cur.json()["sha"]
    r = requests.put(f"https://api.github.com/repos/{TELEMETRY_REPO}/contents/{path}",
                     headers=h, json=body, timeout=45)
    if not r.ok:
        raise RuntimeError(f"outbox commit {path} failed: {r.status_code} {r.text[:160]}")


def render_pack(brand_display: str, label: str, items: List[Dict[str, Any]]) -> str:
    """Paste-ready markdown. One block per post per platform, in posting order,
    each carrying its suggested time, its image, and nothing else to decode."""
    out = [f"# {brand_display}: social pack, {label}", "",
           "Posted BY HAND. Nothing here was sent to Zernio.",
           "Copy the block, attach the named image, post in the window shown.", ""]
    for n, it in enumerate(sorted(items, key=lambda x: (x.get("when") or "", x.get("platform", ""))), 1):
        out.append(f"## {n}. {it.get('platform', '?').upper()}  |  {it.get('when') or 'any time'}")
        img = it.get("image_file") or it.get("image_url")
        out.append(f"Image: {img}" if img else "Image: none (text only)")
        out.append("")
        out.append("```")
        out.append((it.get("text") or "").strip())
        out.append("```")
        out.append("")
    return "\n".join(out)


def export(brand_key: str, brand_display: str, label: str,
           items: List[Dict[str, Any]], token: str,
           images: Optional[Dict[str, bytes]] = None) -> Dict[str, Any]:
    """Commit the pack (+ any real image bytes) under social_outbox/<brand>/<label>/."""
    folder = f"{OUTBOX_ROOT}/{brand_key}/{label}"
    msg = f"social outbox: {brand_display} {label} (manual posting)"
    written = []
    for name, raw in (images or {}).items():
        _put(f"{folder}/{name}", raw, msg, token)
        written.append(name)
    md = render_pack(brand_display, label, items)
    _put(f"{folder}/POSTS.md", md.encode("utf-8"), msg, token)
    written.append("POSTS.md")
    logger.info("[outbox] %s %s: wrote %d file(s), %d post(s), ZERO zernio calls",
                brand_key, label, len(written), len(items))
    return {"ok": True, "folder": folder, "files": written, "posts": len(items)}
