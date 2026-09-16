"""tools/scrapecreators.py - ScrapeCreators social intelligence tools for Marcus.

Wraps the ScrapeCreators REST API (https://api.scrapecreators.com) so Marcus can
autonomously research influencers, competitor brands, and audience signals across
TikTok, Instagram, Facebook, and YouTube during a CrewAI run.

Replaces tools/keyapi.py. KeyAPI repriced to a $189/mo minimum in September 2026
against an actual burn of roughly 20,000 credits per quarter. ScrapeCreators is
pure pay-as-you-go ($47 per 25,000 credits, credits never expire, cached responses
are free), so the same six tools cost materially less with no monthly floor.

Auth: x-api-key: <SCRAPECREATORS_API_KEY>
Cost: 1 credit per call on these endpoints. Every response carries
`credits_remaining`, which this wrapper surfaces in the tool output so runaway
burn is visible in the Crew transcript rather than only on a dashboard.

Tools exposed (each is a CrewAI @tool), signature-compatible with the keyapi
versions they replace:
  - research_tiktok_creator(handle)
  - search_tiktok_creators(keyword, region="US", offset="0")
  - research_instagram_creator(handle)
  - search_instagram_creators(keyword)
  - research_youtube_channel(channel_handle_or_url)
  - research_facebook_page(page_url)

Per-process credit budget: the wrapper enforces a soft cap via the
SCRAPECREATORS_MAX_CALLS_PER_PROCESS env var (default 50) so a runaway Crew
cannot drain the account. Calls beyond the cap return an error string.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Any

import requests
from crewai.tools import tool

logger = logging.getLogger(__name__)

SCRAPECREATORS_BASE_URL = "https://api.scrapecreators.com"
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_CALLS = 50

# Envelope keys ScrapeCreators wraps every payload in. Stripped before summarizing
# so the LLM sees the actual record and not bookkeeping fields.
_ENVELOPE_KEYS = {"success", "credits_remaining", "credits_charged"}

_call_count = 0
_call_lock = threading.Lock()


def _get_api_key() -> str | None:
    return (os.environ.get("SCRAPECREATORS_API_KEY") or "").strip() or None


def _max_calls() -> int:
    raw = os.environ.get("SCRAPECREATORS_MAX_CALLS_PER_PROCESS", "").strip()
    try:
        n = int(raw) if raw else DEFAULT_MAX_CALLS
        return max(1, n)
    except ValueError:
        return DEFAULT_MAX_CALLS


def _check_and_increment_budget() -> str | None:
    """Return None if under budget, or an error message if over."""
    global _call_count
    with _call_lock:
        if _call_count >= _max_calls():
            return (
                f"ERROR: ScrapeCreators per-process call budget exceeded "
                f"({_call_count}/{_max_calls()}). Raise "
                f"SCRAPECREATORS_MAX_CALLS_PER_PROCESS or restart the worker if "
                f"this was an intentional research run."
            )
        _call_count += 1
        return None


def _sc_get(path: str, params: dict[str, Any]) -> dict[str, Any] | str:
    """Low-level GET to api.scrapecreators.com. Returns the parsed JSON dict on
    success, or a human-readable error string on failure (suitable for returning
    to the LLM directly). Never raises."""
    api_key = _get_api_key()
    if not api_key:
        return "ERROR: SCRAPECREATORS_API_KEY environment variable is not set."

    over_budget = _check_and_increment_budget()
    if over_budget:
        return over_budget

    url = f"{SCRAPECREATORS_BASE_URL}{path}"
    headers = {"x-api-key": api_key}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=DEFAULT_TIMEOUT)
    except requests.exceptions.Timeout:
        return f"ERROR: ScrapeCreators timeout on {path} (>{DEFAULT_TIMEOUT}s)"
    except requests.exceptions.RequestException as e:
        return f"ERROR: ScrapeCreators request failed on {path}: {type(e).__name__}: {e}"

    if resp.status_code in (401, 403):
        return (
            f"ERROR: ScrapeCreators rejected the API key ({resp.status_code}). "
            "Verify SCRAPECREATORS_API_KEY is valid."
        )
    if resp.status_code == 402:
        return (
            "ERROR: ScrapeCreators account out of credits (402). "
            "Top up at https://scrapecreators.com."
        )
    if resp.status_code == 429:
        return "ERROR: ScrapeCreators rate limit hit (429). Slow down and retry."
    if resp.status_code >= 400:
        return (
            f"ERROR: ScrapeCreators returned HTTP {resp.status_code} on {path}: "
            f"{resp.text[:300]}"
        )

    try:
        body = resp.json()
    except ValueError:
        return f"ERROR: ScrapeCreators returned non-JSON on {path}: {resp.text[:300]}"

    if not isinstance(body, dict):
        return f"ERROR: ScrapeCreators returned unexpected payload on {path}: {str(body)[:300]}"

    # The API returns HTTP 200 with success=false for "account doesn't exist"
    # style misses. Fail loudly so the agent does not read an empty dict as a
    # real result.
    if body.get("success") is False:
        msg = body.get("message") or body.get("error") or "unknown error"
        return f"ERROR: ScrapeCreators could not fulfil {path}: {msg}"

    # Some endpoints return success=true with a "not found" message and no data.
    msg = str(body.get("message") or "")
    if msg and re.search(r"doesn'?t exist|not found|no results", msg, re.I):
        return f"ERROR: ScrapeCreators found nothing for {path}: {msg}"

    return body


def _summarize(payload: dict[str, Any] | str, label: str) -> str:
    """Render the result as a tight, LLM-friendly string. Strips the response
    envelope, avoids dumping huge raw JSON blobs that blow context, and appends
    the remaining credit balance so burn stays visible in the run transcript.
    If the call failed, returns the error string verbatim."""
    if isinstance(payload, str):
        return payload

    remaining = payload.get("credits_remaining")
    charged = payload.get("credits_charged")

    # Prefer a nested "data" object when present (Instagram), else use every
    # non-envelope key (TikTok, YouTube, Facebook, the search endpoints).
    inner = payload.get("data")
    if not isinstance(inner, dict) or not inner:
        inner = {k: v for k, v in payload.items() if k not in _ENVELOPE_KEYS}

    summary = json.dumps(inner, indent=2, default=str)
    if len(summary) > 8000:
        summary = summary[:8000] + "\n\n[...truncated for context budget...]"

    footer = ""
    if remaining is not None:
        footer = f"\n\n[ScrapeCreators: {charged or 0} credit(s) charged, {remaining} remaining]"

    return f"{label}\n\n{summary}{footer}"


def _youtube_identifier(raw: str) -> str:
    """Normalize a YouTube handle or URL down to what the API accepts.

    The /v1/youtube/channel endpoint takes a bare handle and concatenates
    anything URL-shaped onto its own base, producing a bogus lookup. So pull the
    @handle or the UC... channel id out of a URL before sending it.
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw.startswith("http"):
        m = re.search(r"/(@[A-Za-z0-9._\-]+)", raw)
        if m:
            return m.group(1)
        m = re.search(r"/channel/(UC[A-Za-z0-9_\-]+)", raw)
        if m:
            return m.group(1)
        # Legacy /c/Name or /user/Name forms.
        m = re.search(r"/(?:c|user)/([A-Za-z0-9._\-]+)", raw)
        if m:
            return "@" + m.group(1)
        return raw
    if raw.startswith("UC"):
        return raw
    return raw if raw.startswith("@") else "@" + raw.lstrip("@")


# ---------------------------------------------------------------------------
# TikTok
# ---------------------------------------------------------------------------

@tool("Research TikTok Creator")
def research_tiktok_creator(handle: str) -> str:
    """Look up detailed profile information for a TikTok creator by their unique handle.

    Use this to research influencers, competitor brands, or any TikTok account by
    username. Returns follower count, video count, bio text ("signature"), engagement
    data, and avatar URLs.

    Args:
        handle: TikTok handle WITHOUT the @ prefix (e.g. 'wellwateredwomen', not '@wellwateredwomen').

    Returns: Profile data as JSON string, or an error message.
    """
    handle = (handle or "").strip().lstrip("@")
    if not handle:
        return "ERROR: handle is required (TikTok username without @)."
    result = _sc_get("/v1/tiktok/profile", {"handle": handle})
    return _summarize(result, f"TIKTOK CREATOR PROFILE: @{handle}")


@tool("Search TikTok Creators")
def search_tiktok_creators(keyword: str, region: str = "US", offset: str = "0") -> str:
    """Search for TikTok creators matching a keyword. Returns a list of matching
    profiles with follower counts and basic engagement metrics.

    Use this to discover creators in a niche (e.g., 'christian journaling',
    'bible study', 'faith and motherhood') without knowing specific handles in advance.

    Args:
        keyword: The search term (e.g., 'christian journaling').
        region: Country/region code. Default 'US'. Use 'GB', 'DE', etc. for other markets.
        offset: Pagination cursor. '0' for first page; pass the cursor from a prior
            response to get the next page.

    Returns: List of matching creators as JSON string, or an error message.
    """
    keyword = (keyword or "").strip()
    if not keyword:
        return "ERROR: keyword is required."
    region = (region or "US").strip().upper() or "US"
    params: dict[str, Any] = {"query": keyword, "region": region}
    if offset and str(offset) != "0":
        params["cursor"] = offset
    result = _sc_get("/v1/tiktok/search/users", params)
    return _summarize(result, f"TIKTOK CREATOR SEARCH: keyword={keyword!r} region={region}")


# ---------------------------------------------------------------------------
# Instagram
# ---------------------------------------------------------------------------

@tool("Research Instagram Creator")
def research_instagram_creator(handle: str) -> str:
    """Look up detailed profile information for an Instagram user by username.

    Returns follower count, following count, post count, bio, profile picture,
    and verification status. Useful for sizing an influencer or auditing a brand's IG presence.

    Args:
        handle: Instagram username WITHOUT the @ prefix.

    Returns: Profile data as JSON string, or an error message.
    """
    handle = (handle or "").strip().lstrip("@")
    if not handle:
        return "ERROR: handle is required (Instagram username without @)."
    result = _sc_get("/v1/instagram/profile", {"handle": handle})
    return _summarize(result, f"INSTAGRAM USER PROFILE: @{handle}")


@tool("Search Instagram Creators")
def search_instagram_creators(keyword: str) -> str:
    """Search Instagram users by keyword. Returns matching user accounts.

    Use this to find potential influencers or brand pages in a niche when you
    don't know specific handles. Pair with research_instagram_creator for deep dives.

    Args:
        keyword: Search term (e.g., 'faith journaling').

    Returns: List of matching users as JSON string, or an error message.
    """
    keyword = (keyword or "").strip()
    if not keyword:
        return "ERROR: keyword is required."
    result = _sc_get("/v1/instagram/search/profiles", {"query": keyword})
    return _summarize(result, f"INSTAGRAM USER SEARCH: keyword={keyword!r}")


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------

@tool("Research YouTube Channel")
def research_youtube_channel(channel_handle_or_url: str) -> str:
    """Look up details for a YouTube channel by handle (e.g. '@WellWateredWomen')
    or full channel URL. Returns subscriber count, view count, description,
    verification status, and channel id.

    Single call, unlike the two-step resolve the previous provider needed.

    Args:
        channel_handle_or_url: '@channelhandle', 'https://youtube.com/@handle', or
            a /channel/UC... URL.

    Returns: Channel data as JSON string, or an error message.
    """
    raw = (channel_handle_or_url or "").strip()
    if not raw:
        return "ERROR: channel handle or URL is required."
    identifier = _youtube_identifier(raw)
    if not identifier:
        return f"ERROR: could not parse a YouTube handle or channel id from {raw!r}."
    result = _sc_get("/v1/youtube/channel", {"handle": identifier})
    return _summarize(result, f"YOUTUBE CHANNEL: {raw} (lookup={identifier})")


# ---------------------------------------------------------------------------
# Facebook
# ---------------------------------------------------------------------------

@tool("Research Facebook Page")
def research_facebook_page(page_url: str) -> str:
    """Look up details for a public Facebook page or profile by URL.

    Returns page name, follower/likes counts, category, page id, creation date,
    and whether the page is currently running ads.

    Args:
        page_url: Full Facebook URL (e.g., 'https://www.facebook.com/wellwateredwomen').

    Returns: Page data as JSON string, or an error message.
    """
    url = (page_url or "").strip()
    if not url:
        return "ERROR: page_url is required."
    if not url.startswith("http"):
        url = "https://www.facebook.com/" + url.lstrip("/")
    result = _sc_get("/v1/facebook/profile", {"url": url})
    return _summarize(result, f"FACEBOOK PAGE: {url}")


# ---------------------------------------------------------------------------
# Convenience: status / call-count probe (NOT a CrewAI tool)
# ---------------------------------------------------------------------------

def scrapecreators_status() -> dict[str, Any]:
    """Lightweight observability - used by /admin or /bridge endpoints if needed."""
    return {
        "configured": bool(_get_api_key()),
        "calls_this_process": _call_count,
        "max_calls_per_process": _max_calls(),
        "base_url": SCRAPECREATORS_BASE_URL,
    }


SCRAPECREATORS_TOOLS = [
    research_tiktok_creator,
    search_tiktok_creators,
    research_instagram_creator,
    search_instagram_creators,
    research_youtube_channel,
    research_facebook_page,
]
