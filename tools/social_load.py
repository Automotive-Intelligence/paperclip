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
