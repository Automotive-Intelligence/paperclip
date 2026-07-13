"""tools/social_load.py — THE one social loader (file 121, Phase 1: The Pipe).

Every scheduled social post rides through load_jobs(): Zernio for own brands,
Buffer DRAFTS for P&P (the draft is the client gate). Guarantees:
  * DRY-RUN by default; commit is explicit.
  * Queue guard: refuses a post when the rail already has one for the same
    brand+platform+day (override: allow_stack=True).
  * UTM discipline: every http(s) link in every post is tagged. No UTM, no schedule.
  * Registry: one JSONL row per scheduled post (the Phase-3 attribution join key).
  * WD hard block until config/social_load.json wd_rename_done flips true.

Spec: ~/avo-telemetry/marketing_deliverables/121_social_publishing_os.md
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone as _tz
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from zoneinfo import ZoneInfo

_URL_RE = re.compile(r"https?://[^\s\)\]\}>\"']+")
_CENTRAL = ZoneInfo("America/Chicago")


# ---------------------------------------------------------------- UTM
def add_utm(url: str, platform: str, brand: str, content_id: str,
            entry_point: str, slot: str) -> str:
    parts = urlsplit(url)
    q = parse_qsl(parts.query, keep_blank_values=True)
    q += [("utm_source", platform), ("utm_medium", "social"),
          ("utm_campaign", f"{brand}_{content_id}"),
          ("utm_content", f"{entry_point}-{slot}")]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


def tag_links(text: str, platform: str, brand: str, content_id: str,
              entry_point: str, slot: str) -> str:
    return _URL_RE.sub(
        lambda m: add_utm(m.group(0), platform, brand, content_id, entry_point, slot),
        text)


# ---------------------------------------------------------------- registry
def registry_path() -> str:
    return os.environ.get("SOCIAL_REGISTRY_PATH") or os.path.expanduser(
        "~/avo-telemetry/social_registry.jsonl")


def append_registry(row: dict) -> None:
    path = registry_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out = {"ts": datetime.now(_tz.utc).isoformat(timespec="seconds"), **row}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- routing
class WdBlockedError(RuntimeError):
    """WD content refused: handles are still @callingdigital (file 120)."""


class NoRailError(RuntimeError):
    """Brand has no distribution rail yet (e.g. Book'd until Ryan's key)."""


BRAND_ALIASES = {
    "automotive intelligence": "avi",
    "the ai phone guy": "aipg", "ai phone guy": "aipg",
    "worship digital": "wd",
    "agent empire": "agent_empire",
    "book'd": "bookd",
    "paper & purpose": "paperandpurpose", "paper and purpose": "paperandpurpose",
}
_CFG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "config", "social_load.json")


def canonical_brand(brand: str) -> str:
    b = brand.strip().lower()
    return BRAND_ALIASES.get(b, b)


def load_config() -> dict:
    try:
        return json.load(open(_CFG_PATH, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def route_for_brand(brand: str, cfg: Optional[dict] = None) -> str:
    b = canonical_brand(brand)
    if b == "paperandpurpose":
        return "buffer"
    if b == "bookd":
        raise NoRailError("Book'd has no rail: Ryan posts himself or issues a scoped key (file 121).")
    if b == "wd":
        conf = cfg if cfg is not None else load_config()
        if not conf.get("wd_rename_done"):
            raise WdBlockedError(
                "WD is hard-blocked: handles are still @callingdigital. "
                "Complete marketing_deliverables/120_wd_handle_rename_runbook.md, "
                "then flip wd_rename_done in config/social_load.json.")
    return "zernio"


# ---------------------------------------------------------------- queue guard
def _rail_local_day(iso_utc: str) -> Optional[str]:
    if not iso_utc:
        return None
    try:
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(_CENTRAL).date().isoformat()


def find_conflicts(existing: List[dict], platform: str, day: str,
                   account_id: Optional[str] = None) -> List[dict]:
    """Rows in the rail's live queue already occupying brand+platform+local-day.

    Zernio rows carry scheduledFor (UTC) + platforms[{platform, accountId}] where
    accountId may be an object (extract ._id). Buffer rows carry dueAt/scheduledAt
    + channelId. The rail's calendar is the single source of truth; this guard is
    why nobody cross-checks documents (file 121)."""
    hits = []
    for p in existing:
        if (p.get("status") or "").lower() not in ("scheduled", "pending", "queued", "draft"):
            continue
        when = p.get("scheduledFor") or p.get("dueAt") or p.get("scheduledAt") or ""
        if _rail_local_day(str(when)) != day:
            continue
        plats = p.get("platforms") or []
        if plats:                                    # zernio shape
            for c in plats:
                if c.get("platform") != platform:
                    continue
                aid = c.get("accountId")
                aid = aid.get("_id") if isinstance(aid, dict) else aid
                if account_id is None or str(aid) == str(account_id):
                    hits.append(p)
                    break
        else:                                        # buffer shape
            if account_id is None or str(p.get("channelId")) == str(account_id):
                hits.append(p)
    return hits
