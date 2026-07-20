"""services/studio_social_publish.py -- schedule a gated brand batch to Zernio.

Builds PostJob dicts (per post x per platform) with the RIGHT account_id resolved
per brand+platform (the missing account_id = cross-brand fan-out foot-gun the blog
runlog documented), the file-103 native-peak stagger time, and a HARD X 280-char
guard, then hands them to the ONE loader (tools/social_load via
services/social_load_service.run_social_load) which owns UTMs, the gap-fill queue
guard, the WD-rename gate, and Book'd's NoRail hold.

The X 280 guard is the real bug from file 139 / 142 / the blog runlog, fixed HERE
in the social publish path (not in the hot social_load.py another crew is editing):
X wraps every link to t.co's fixed 23 chars, so we count links as 23, which neither
false-positives on a long UTM nor masks a genuine over-limit.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s\)\]\}>\"']+")
_TCO_LEN = 23           # X shortens EVERY link to a fixed 23 chars
_X_LIMIT = 280

# our config platform name -> Zernio/social_load platform id
_PLATFORM_TO_ZERNIO = {"x": "twitter", "twitter": "twitter",
                       "linkedin": "linkedin", "facebook": "facebook",
                       "instagram": "instagram"}


def tco_length(text: str) -> int:
    """Length of `text` as X counts it: every URL collapses to 23 chars."""
    stripped = _URL_RE.sub("", text)
    n_urls = len(_URL_RE.findall(text))
    return len(stripped) + n_urls * _TCO_LEN


def x_within_limit(text: str) -> bool:
    return tco_length(text) <= _X_LIMIT


def _pid_of(acct: Dict[str, Any]) -> str:
    p = acct.get("profileId") or acct.get("profile_id")
    return str(p.get("_id") if isinstance(p, dict) else (p or ""))


def resolve_accounts(profiles: List[Dict[str, Any]], accts: List[Dict[str, Any]],
                     profile_name: str) -> Dict[str, str]:
    """{zernio_platform: account_id} for the given Zernio profile display name.
    Case-insensitive profile match (WD's profile is the legacy 'Calling Digital').
    Empty if the profile is not connected -- caller then skips the brand."""
    prof_id = next((pid for pname, pid in
                    {p.get("name"): p.get("_id") for p in profiles}.items()
                    if (pname or "").lower() == profile_name.lower()), None)
    if not prof_id:
        return {}
    return {a["platform"]: a["_id"] for a in accts if _pid_of(a) == str(prof_id)}


def week_day_offsets(count: int) -> List[int]:
    """Spread `count` posts across the week's posting days (offsets from Monday).
    3 posts -> Tue/Thu/Sat, matching the file-103 cadence the laptop engine used.
    The loader's queue guard drops any that collide with an already-filled slot,
    so this only needs to be a sensible spread, not a precise per-brand grid."""
    pool = [1, 3, 5, 2, 4, 6]           # Tue, Thu, Sat, Wed, Fri, Sun
    return sorted(pool[:max(1, count)])


def build_jobs(
    brand_cfg: Dict[str, Any],
    posts: List[Dict[str, Any]],
    week_monday: str,
    accounts: Dict[str, str],
    media_by_key: Dict[str, str],
    stagger: Dict[str, Dict[str, str]],
    content_id: str,
) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str, str]]]:
    """Return (jobs, skips) where each skip is (post_key, platform, reason)."""
    display = brand_cfg["display_name"].lower()
    grid = stagger.get(display, {})
    offsets = week_day_offsets(len(posts))
    jobs: List[Dict[str, Any]] = []
    skips: List[Tuple[str, str, str]] = []
    from datetime import date, timedelta
    y, m, d = (int(x) for x in week_monday.split("-"))
    monday = date(y, m, d)
    for i, post in enumerate(posts):
        day = (monday + timedelta(days=offsets[i % len(offsets)])).isoformat()
        media = [media_by_key[post["key"]]] if media_by_key.get(post["key"]) else []
        for conf_plat, text in post["platforms"].items():
            zplat = _PLATFORM_TO_ZERNIO.get(conf_plat, conf_plat)
            aid = accounts.get(zplat)
            if not aid:
                skips.append((post["key"], conf_plat, "no connected account for platform"))
                continue
            hhmm = grid.get(zplat)
            if not hhmm:
                skips.append((post["key"], conf_plat, "no stagger slot in file-103 grid"))
                continue
            if zplat == "twitter" and not x_within_limit(text):
                skips.append((post["key"], conf_plat,
                              f"X over {_X_LIMIT} (t.co-counted {tco_length(text)})"))
                continue
            jobs.append({
                "brand": brand_cfg["display_name"], "platform": zplat, "content": text,
                "scheduled_for": f"{day}T{hhmm}:00", "tz": "America/Chicago",
                "content_id": content_id, "entry_point": "studio",
                "account_id": aid, "media_urls": media,
            })
    return jobs, skips
