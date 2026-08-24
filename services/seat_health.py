"""services/seat_health.py -- is anyone actually reading the flags we post?

AVO's seats coordinate by writing `🏁 FLAG FOR: <seat>` into their OWN state file;
every other seat pull-scans those files at session start. That protocol has one silent
failure mode: a flag posted to a seat that has gone quiet is a message dropped into a
drawer nobody opens. Nothing errors. Nothing alerts. The sender believes it was
delivered, exactly the way the lead funnel used to believe a lead was captured.

On 2026-08-23 a first look found 14 of 23 seats untouched for over two weeks. This makes
that visible and keeps it visible.

DIRECTION MATTERS, and it is easy to get backwards (B&T did, on the first pass):
  - flags INSIDE a seat's own file are OUTBOUND -- asks that seat made of others.
  - flags WAITING on a seat are found by scanning EVERY file for a target that resolves
    to it through seats.yaml aliases.
The second number is the dangerous one: unread mail. The first is that seat's own asks
going stale.

Everything is derived from ground truth (the committed files + GitHub commit dates), so
there is nothing to maintain and nothing that can rot.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_REPO = "salesdroid/avo-telemetry"
_API = f"https://api.github.com/repos/{_REPO}"
_COLD_DAYS = 14
_FLAG_RE = re.compile(r"🏁\s*FLAG FOR:?\s*(.+)")
_RESOLVED_RE = re.compile(r"✅\s*(RESOLVED|CLOSED|DELIVERED|DONE)", re.IGNORECASE)


def _token() -> str:
    return (os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
            or os.getenv("SLIPSTREAM_GH_TOKEN") or "").strip()


def _seats() -> List[Dict[str, Any]]:
    """The seat registry itself -- seats.yaml is the single source of truth that
    flag_router and morning_briefing already read at runtime. We read the same file
    rather than keeping a second roster, because two rosters drift."""
    from services.avo_state import _fetch
    raw = _fetch("seats.yaml")
    if not raw:
        return []
    try:
        import yaml
        return (yaml.safe_load(raw) or {}).get("seats", []) or []
    except Exception:
        logger.warning("[seats] seats.yaml unparseable")
        return []


def _last_commit_days(path: str) -> Optional[float]:
    """Days since the owned file last changed. Uses the commits API because the
    container has no git checkout of avo-telemetry."""
    tok = _token()
    if not tok:
        return None
    try:
        r = requests.get(f"{_API}/commits", timeout=20,
                         params={"path": path, "per_page": 1},
                         headers={"Authorization": f"Bearer {tok}",
                                  "Accept": "application/vnd.github+json"})
        if not r.ok:
            return None
        data = r.json() or []
        if not data:
            return None
        when = data[0]["commit"]["committer"]["date"]
    except Exception:
        logger.warning("[seats] commit lookup failed for %s", path)
        return None
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(when.replace("Z", "+00:00"))
        return round((datetime.now(timezone.utc) - dt).total_seconds() / 86400, 1)
    except ValueError:
        return None


def _alias_index(seats: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """(normalized alias, canonical seat) pairs, longest alias first so a specific
    match beats a generic substring ('Build & Tech' before 'Tech')."""
    pairs: List[Tuple[str, str]] = []
    for s in seats:
        canon = s.get("canonical_name") or ""
        names = set(s.get("aliases") or []) | {canon}
        for a in names:
            n = re.sub(r"[^a-z0-9&]+", "", str(a).lower())
            if n:
                pairs.append((n, canon))
    return sorted(pairs, key=lambda p: -len(p[0]))


def _resolve(target_text: str, index: List[Tuple[str, str]]) -> Optional[str]:
    t = re.sub(r"[^a-z0-9&]+", "", target_text.lower())
    if not t:
        return None
    for alias, canon in index:
        if alias in t:
            return canon
    return None


def _flag_blocks(text: str) -> List[Tuple[str, bool]]:
    """(target_text, resolved) for each flag in a file. A flag is resolved when its
    block carries a ✅ RESOLVED/CLOSED/DELIVERED/DONE marker before the next flag."""
    out: List[Tuple[str, bool]] = []
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if _FLAG_RE.search(ln)]
    for n, i in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        block = "\n".join(lines[i:end])
        m = _FLAG_RE.search(lines[i])
        target = (m.group(1) if m else "")[:120]
        # the marker can sit on the flag line itself ("✅ RESOLVED (was: FLAG FOR ...)")
        out.append((target, bool(_RESOLVED_RE.search(block))))
    return out


def seat_health() -> Dict[str, Any]:
    """Per-seat coordination health, derived. Never raises: a failure yields an empty
    roster rather than a broken page."""
    try:
        seats = _seats()
        if not seats:
            return {"ok": False, "error": "seats.yaml unreadable", "seats": []}
        index = _alias_index(seats)

        from services.avo_state import _fetch
        posted: Dict[str, int] = {}
        waiting: Dict[str, int] = {}
        unrouted = 0

        for s in seats:
            f = s.get("owned_file") or ""
            canon = s.get("canonical_name") or ""
            text = _fetch(f) if f else ""
            open_flags = [(t, r) for (t, r) in _flag_blocks(text or "") if not r]
            posted[canon] = len(open_flags)
            for target_text, _ in open_flags:
                tgt = _resolve(target_text, index)
                if tgt:
                    waiting[tgt] = waiting.get(tgt, 0) + 1
                else:
                    unrouted += 1

        rows: List[Dict[str, Any]] = []
        for s in seats:
            canon = s.get("canonical_name") or ""
            f = s.get("owned_file") or ""
            age = _last_commit_days(f) if f else None
            cold = age is None or age > _COLD_DAYS
            rows.append({
                "seat": canon,
                "owned_file": f,
                "days_since_activity": age,
                "cold": cold,
                "flags_posted_open": posted.get(canon, 0),
                "flags_waiting": waiting.get(canon, 0),
                # unread mail: someone asked this seat for something and it went quiet
                "unread": waiting.get(canon, 0) if cold else 0,
            })

        rows.sort(key=lambda r: (-r["unread"], -(r["days_since_activity"] or 9999)))
        return {
            "ok": True,
            "cold_days": _COLD_DAYS,
            "totals": {
                "seats": len(rows),
                "cold": sum(1 for r in rows if r["cold"]),
                "open_flags": sum(r["flags_posted_open"] for r in rows),
                "unread_flags": sum(r["unread"] for r in rows),
                "unrouted_flags": unrouted,
            },
            "seats": rows,
        }
    except Exception as e:
        logger.exception("[seats] health build failed")
        return {"ok": False, "error": str(e), "seats": []}
