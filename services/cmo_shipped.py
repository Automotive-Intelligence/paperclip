"""services/cmo_shipped.py -- the REAL "what shipped" signal for the CMO Daily.

Why this exists
---------------
The CMO Daily brief used to render entirely from a hand-maintained
avo-telemetry/cmo_daily_state.json that nobody kept current (it went stale on
2026-06-24). So on 2026-08-02 the brief reported "Producing routine not yet
cron'd" and "nothing shipped" for WD/AvI/BAE -- which was FALSE: the Railway
Slipstream blog engine auto-published all weekend (AvI + AIPG Friday, WD merged
Saturday). The brief was reporting a placeholder, not reality.

This module reads the ACTUAL engine output instead:

  * blog posts merged to each brand's Next.js repo `main` inside the lookback
    window -- salesdroid/{automotive-intelligence, ai-phone-guy-site,
    buildagentempire, worship-digital} -- via the GitHub commits API using the
    same SLIPSTREAM_GH_TOKEN the engine publishes with.
  * social posts distributed via the one loader, logged to avo-telemetry
    social_registry.jsonl (read with the flag_router's authenticated path).
  * "held / awaiting you" = OPEN blog PRs on each repo (a human-merge WD post
    waiting on Michael, or a build-gate HOLD) -- a real signal, not a chore.

Fail-safe direction: if a repo query fails (missing token, transport error), the
brand is marked signal_ok=False and the brief says "engine signal unavailable"
for that brand. It NEVER falls back to the false "nothing shipped."

Every network call is a module seam so tests never hit the wire.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_GH_API = "https://api.github.com/repos"
_REQUEST_TIMEOUT = 20
_DEFAULT_LOOKBACK_HOURS = 72  # weekend-safe: a Sunday brief still shows Fri/Sat ships

# Open-PR head-ref prefixes that mean "a blog post is held awaiting merge".
# Everything else open on the repo (seo/, copy/, vercel/, iris-*) is not a
# CMO-held content item and must not be dumped into the brief.
_BLOG_BRANCH_PREFIXES = ("blog/", "slipstream/", "content/")

# Commit-message prefixes that are maintenance, not a shipped post. The engine's
# publish commits are "content: ..." / "content(blog): ..."; its cleanup commits
# are "dedupe: ...". Excluding these collapses a re-run's dedupe pass into the
# single post it cleaned up.
_MAINT_RE = re.compile(
    r"^\s*(dedupe|chore|ci|merge|revert|style|refactor|docs|build|test|seo|"
    r"format|cleanup|fix|bump|hotfix|deps?)\b",
    re.IGNORECASE,
)
_TITLE_PREFIX_RE = re.compile(
    r"^(content|feat|add|blog|post)\s*(\([^)]*\))?\s*:\s*", re.IGNORECASE
)
_TITLE_VERB_RE = re.compile(r"^(add|added|adds|publish|published|new)\s+", re.IGNORECASE)
_TITLE_PRNUM_RE = re.compile(r"\s*\(#\d+\)\s*$")


@dataclass(frozen=True)
class Brand:
    key: str                       # canonical brand key used in the brief
    name: str                      # display name
    autonomy: str                  # auto | partial | oversight
    light: str                     # status light glyph
    repo: Optional[str]            # salesdroid/... or None (no Slipstream rail)
    blog_path: Optional[str]       # blog dir (mdx) or single posts file (ts array)
    social_keys: Tuple[str, ...]   # brand keys as they appear in social_registry
    domain: Optional[str] = None


# Canonical brand set for the brief. The four Slipstream brands map to their
# Next.js repos; Book'd has no Slipstream rail (Cloudflare Pages, oversight-only)
# so it carries social-only signal.
BRANDS: List[Brand] = [
    Brand("worshipdigital", "Worship Digital", "auto", "\U0001F7E2",
          "salesdroid/worship-digital", "src/content/posts.ts",
          ("wd", "wd_legacy_cd"), "worshipdigital.co"),
    Brand("autointelligence", "Automotive Intelligence", "auto", "\U0001F7E2",
          "salesdroid/automotive-intelligence", "src/content/blog",
          ("avi",), "automotiveintelligence.io"),
    Brand("agentempire", "Build Agent Empire", "auto", "\U0001F7E2",
          "salesdroid/buildagentempire", "src/content/blog",
          ("agent_empire",), "buildagentempire.com"),
    Brand("aiphoneguy", "AI Phone Guy", "partial", "\U0001F7E1",
          "salesdroid/ai-phone-guy-site", "src/content/blog",
          ("aipg",), "theaiphoneguy.com"),
    Brand("bookd", "Book'd", "oversight", "⚪",
          None, None, ("bookd",), "bookd.cx"),
]


# ---------------------------------------------------------------------------
# GitHub (blog repos) -- uses SLIPSTREAM_GH_TOKEN (Contents + PR read on repos)
# ---------------------------------------------------------------------------

def _gh_token() -> str:
    return (os.environ.get("SLIPSTREAM_GH_TOKEN") or "").strip()


def _default_gh_get(url: str, token: str) -> Any:
    r = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "avo-cmo-daily/shipped",
        },
        timeout=_REQUEST_TIMEOUT,
    )
    if not r.ok:
        raise RuntimeError(f"GET {url} -> {r.status_code}: {r.text[:200]}")
    return r.json() if r.text else []


def list_blog_commits(
    repo: str,
    blog_path: str,
    since_iso: str,
    token: str,
    gh_get: Callable[[str, str], Any] = _default_gh_get,
) -> List[Dict[str, Any]]:
    """Commits on `main` touching `blog_path` since `since_iso`. Squash-merged
    PRs collapse to one commit each, so this is one call per brand."""
    url = (
        f"{_GH_API}/{repo}/commits?sha=main"
        f"&path={blog_path}&since={since_iso}&per_page=30"
    )
    data = gh_get(url, token) or []
    out: List[Dict[str, Any]] = []
    for c in data:
        commit = c.get("commit") or {}
        out.append({
            "sha": (c.get("sha") or "")[:7],
            "message": (commit.get("message") or ""),
            "date": ((commit.get("committer") or {}).get("date")
                     or (commit.get("author") or {}).get("date") or ""),
        })
    return out


def list_open_blog_prs(
    repo: str,
    token: str,
    gh_get: Callable[[str, str], Any] = _default_gh_get,
) -> List[Dict[str, Any]]:
    """Open PRs whose head branch is a blog branch -> a post held awaiting merge
    (WD human-merge) or a build-gate hold. Non-blog PRs are ignored."""
    url = f"{_GH_API}/{repo}/pulls?state=open&per_page=30"
    data = gh_get(url, token) or []
    out: List[Dict[str, Any]] = []
    for pr in data:
        ref = ((pr.get("head") or {}).get("ref") or "").lower()
        if not ref.startswith(_BLOG_BRANCH_PREFIXES):
            continue
        out.append({
            "number": pr.get("number"),
            "title": pr.get("title") or "",
            "ref": ref,
            "url": pr.get("html_url") or "",
        })
    return out


def _clean_title(message: str) -> str:
    t = (message or "").split("\n", 1)[0].strip()
    t = _TITLE_PRNUM_RE.sub("", t)
    t = _TITLE_PREFIX_RE.sub("", t)
    t = _TITLE_VERB_RE.sub("", t)
    return t.strip().strip('"').strip("'").strip()


def _is_maintenance(message: str) -> bool:
    first = (message or "").split("\n", 1)[0]
    return bool(_MAINT_RE.match(first))


def shipped_titles(commits: List[Dict[str, Any]], cap: int = 3) -> Tuple[List[str], int]:
    """Distinct, newest-first shipped post titles (maintenance commits excluded,
    deduped by normalized title). Returns (titles_capped, total_distinct)."""
    seen: set[str] = set()
    titles: List[str] = []
    for c in commits:  # GitHub returns newest-first
        if _is_maintenance(c.get("message", "")):
            continue
        title = _clean_title(c.get("message", ""))
        if not title:
            continue
        norm = re.sub(r"\s+", " ", title).lower()
        if norm in seen:
            continue
        seen.add(norm)
        titles.append(title)
    return titles[:cap], len(titles)


# ---------------------------------------------------------------------------
# Social (avo-telemetry social_registry.jsonl)
# ---------------------------------------------------------------------------

def _parse_iso(ts: str) -> Optional[_dt.datetime]:
    if not ts:
        return None
    try:
        dt = _dt.datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def social_counts(registry_text: str, since: _dt.datetime) -> Dict[str, int]:
    """Count social_registry rows per brand key with ts/scheduled_for >= since."""
    counts: Dict[str, int] = {}
    for line in (registry_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        when = _parse_iso(row.get("scheduled_for") or row.get("ts") or "")
        if when is None or when < since:
            continue
        brand = row.get("brand") or "?"
        counts[brand] = counts.get(brand, 0) + 1
    return counts


def _load_registry_text() -> str:
    """Read social_registry.jsonl from avo-telemetry via the flag_router's
    authenticated GitHub path. Returns '' on any failure (social simply reads 0)."""
    try:
        from services.flag_router import _fetch_telemetry_path
        return _fetch_telemetry_path("social_registry.jsonl") or ""
    except Exception as e:  # noqa: BLE001 - social is best-effort, never fatal
        logger.warning("[cmo-shipped] social_registry read failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

def collect(
    now: Optional[_dt.datetime] = None,
    lookback_hours: Optional[int] = None,
    *,
    gh_token: Optional[str] = None,
    registry_text: Optional[str] = None,
    gh_get: Callable[[str, str], Any] = _default_gh_get,
) -> List[Dict[str, Any]]:
    """Build the per-brand shipped/held reality for the brief.

    Returns a list (canonical BRANDS order) of dicts:
      {key, name, autonomy, light,
       posts:[title...], post_count:int, social:int,
       held:[{number,title,url}...],
       signal_ok:bool}       # False => repo query failed; render "unavailable",
                             #          NEVER the false "nothing shipped".
    """
    now = now or _dt.datetime.now(_dt.timezone.utc)
    hours = lookback_hours if lookback_hours is not None else int(
        os.environ.get("CMO_DAILY_LOOKBACK_HOURS", _DEFAULT_LOOKBACK_HOURS)
    )
    since_dt = now - _dt.timedelta(hours=hours)
    since_iso = since_dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    token = gh_token if gh_token is not None else _gh_token()
    reg_text = registry_text if registry_text is not None else _load_registry_text()
    social = social_counts(reg_text, since_dt)

    rows: List[Dict[str, Any]] = []
    for b in BRANDS:
        social_n = sum(social.get(k, 0) for k in b.social_keys)
        row: Dict[str, Any] = {
            "key": b.key, "name": b.name, "autonomy": b.autonomy, "light": b.light,
            "posts": [], "post_count": 0, "social": social_n,
            "held": [], "signal_ok": True,
        }
        if not b.repo:
            # No Slipstream rail (Book'd) -- social-only, blog signal N/A but OK.
            rows.append(row)
            continue
        if not token:
            row["signal_ok"] = False
            rows.append(row)
            continue
        try:
            commits = list_blog_commits(b.repo, b.blog_path or "", since_iso, token, gh_get)
            posts, total = shipped_titles(commits)
            row["posts"] = posts
            row["post_count"] = total
        except Exception as e:  # noqa: BLE001 - degrade to "unavailable", not false-negative
            logger.warning("[cmo-shipped] %s blog signal failed: %s", b.key, e)
            row["signal_ok"] = False
        try:
            row["held"] = list_open_blog_prs(b.repo, token, gh_get)
        except Exception as e:  # noqa: BLE001 - held is best-effort
            logger.warning("[cmo-shipped] %s open-PR signal failed: %s", b.key, e)
        rows.append(row)
    return rows


def shipped_lines(row: Dict[str, Any]) -> List[str]:
    """Human-readable 'Shipped (auto)' cell for one brand row."""
    if not row.get("signal_ok", True):
        return ["engine signal unavailable (check SLIPSTREAM_GH_TOKEN / Slipstream)"]
    lines: List[str] = []
    for title in row.get("posts", []):
        lines.append(f"blog: {title}")
    extra = row.get("post_count", 0) - len(row.get("posts", []))
    if extra > 0:
        lines.append(f"+{extra} more blog post{'s' if extra != 1 else ''}")
    if row.get("social", 0):
        lines.append(f"{row['social']} social post{'s' if row['social'] != 1 else ''} (Zernio)")
    return lines


def held_lines(row: Dict[str, Any]) -> List[str]:
    """Human-readable 'Held / awaiting you' cell for one brand row."""
    out: List[str] = []
    for pr in row.get("held", []):
        num = pr.get("number")
        out.append(f"PR #{num} awaiting merge: {pr.get('title', '')}".strip())
    return out
