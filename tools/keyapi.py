"""tools/keyapi.py — KeyAPI social intelligence tools for Marcus.

Wraps KeyAPI's REST API (https://api.keyapi.ai/v1) so Marcus can autonomously
research influencers, competitor brands, and audience signals across TikTok,
Instagram, Facebook, and YouTube during a CrewAI run.

Auth: Authorization: Bearer <KEYAPI_API_KEY>
Cost: ~1 credit per call — see https://www.keyapi.ai for current pricing.

Tools exposed (each is a CrewAI @tool):
  - research_tiktok_creator(handle)
  - search_tiktok_creators(keyword, max_results=10)
  - research_instagram_creator(handle)
  - search_instagram_creators(keyword, max_results=10)
  - research_youtube_channel(channel_handle_or_url)
  - research_facebook_page(page_url)

Per-process credit budget: the wrapper enforces a soft cap via the
KEYAPI_MAX_CALLS_PER_PROCESS env var (default 50) so a runaway Crew can't
drain the account. Calls beyond the cap return an error string.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

import requests
from crewai.tools import tool

logger = logging.getLogger(__name__)

KEYAPI_BASE_URL = "https://api.keyapi.ai/v1"
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_CALLS = 50

_call_count = 0
_call_lock = threading.Lock()


def _get_api_key() -> str | None:
    return (os.environ.get("KEYAPI_API_KEY") or "").strip() or None


def _max_calls() -> int:
    raw = os.environ.get("KEYAPI_MAX_CALLS_PER_PROCESS", "").strip()
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
                f"ERROR: KeyAPI per-process call budget exceeded "
                f"({_call_count}/{_max_calls()}). Raise KEYAPI_MAX_CALLS_PER_PROCESS "
                f"or restart the worker if this was an intentional research run."
            )
        _call_count += 1
        return None


def _keyapi_get(path: str, params: dict[str, Any]) -> dict[str, Any] | str:
    """Low-level GET to api.keyapi.ai. Returns parsed JSON dict on success,
    or a human-readable error string on failure (suitable for returning to
    the LLM directly). Never raises."""
    api_key = _get_api_key()
    if not api_key:
        return "ERROR: KEYAPI_API_KEY environment variable is not set."

    over_budget = _check_and_increment_budget()
    if over_budget:
        return over_budget

    url = f"{KEYAPI_BASE_URL}{path}"
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=DEFAULT_TIMEOUT)
    except requests.exceptions.Timeout:
        return f"ERROR: KeyAPI timeout on {path} (>{DEFAULT_TIMEOUT}s)"
    except requests.exceptions.RequestException as e:
        return f"ERROR: KeyAPI request failed on {path}: {type(e).__name__}: {e}"

    if resp.status_code == 401:
        return "ERROR: KeyAPI rejected the API key (401). Verify KEYAPI_API_KEY is valid."
    if resp.status_code == 402:
        return "ERROR: KeyAPI account out of credits (402). Top up at https://keyapi.ai/app/dashboard."
    if resp.status_code == 429:
        return "ERROR: KeyAPI rate limit hit (429). Slow down or upgrade tier."
    if resp.status_code >= 400:
        return f"ERROR: KeyAPI returned HTTP {resp.status_code} on {path}: {resp.text[:300]}"

    try:
        body = resp.json()
    except ValueError:
        return f"ERROR: KeyAPI returned non-JSON on {path}: {resp.text[:300]}"

    api_code = body.get("code")
    if api_code not in (0, None):
        msg = body.get("message", "unknown error")
        return f"ERROR: KeyAPI logical error on {path} (code={api_code}): {msg}"

    return body


def _summarize(payload: dict[str, Any] | str, label: str) -> str:
    """Render the result as a tight, LLM-friendly string. Avoids dumping huge
    raw JSON blobs that blow context. If the call failed, returns the error
    string verbatim."""
    if isinstance(payload, str):
        return payload
    data = payload.get("data") or {}
    summary = json.dumps(data, indent=2, default=str)
    if len(summary) > 8000:
        summary = summary[:8000] + "\n\n[...truncated for context budget...]"
    return f"{label}\n\n{summary}"


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
    result = _keyapi_get("/tiktok/influencer/detail", {"unique_id": handle})
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
    result = _keyapi_get(
        "/tiktok/influencer/search",
        {"keyword": keyword, "region": region, "offset": offset or "0"},
    )
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
    result = _keyapi_get("/instagram/fetch_user_info", {"username": handle})
    return _summarize(result, f"INSTAGRAM USER PROFILE: @{handle}")


@tool("Search Instagram Creators")
def search_instagram_creators(keyword: str) -> str:
    """Search Instagram users by keyword. Returns matching user accounts.

    Use this to find potential influencers or brand pages in a niche when you
    don't know specific handles. Pair with research_instagram_creator for deep dives.
    Costs 2 credits per call.

    Args:
        keyword: Search term (e.g., 'faith journaling').

    Returns: List of matching users as JSON string, or an error message.
    """
    keyword = (keyword or "").strip()
    if not keyword:
        return "ERROR: keyword is required."
    result = _keyapi_get("/instagram/search_users", {"query": keyword})
    return _summarize(result, f"INSTAGRAM USER SEARCH: keyword={keyword!r}")


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------

@tool("Research YouTube Channel")
def research_youtube_channel(channel_handle_or_url: str) -> str:
    """Look up details for a YouTube channel by handle (e.g. '@WellWateredWomen')
    or full channel URL. Returns subscriber count, view count, description,
    and join date.

    Two-step lookup under the hood: first resolves the handle/URL to a channel ID,
    then fetches the channel description. Costs ~2 credits per call.

    Args:
        channel_handle_or_url: '@channelhandle', 'https://youtube.com/@handle', or
            a /channel/UC... URL.

    Returns: Channel data as JSON string, or an error message.
    """
    raw = (channel_handle_or_url or "").strip()
    if not raw:
        return "ERROR: channel handle or URL is required."

    if raw.startswith("http"):
        id_lookup = _keyapi_get("/youtube/get_channel_id_from_url", {"url": raw})
    else:
        handle = raw.lstrip("@")
        id_lookup = _keyapi_get("/youtube/get_channel_id", {"channel_name": handle})

    if isinstance(id_lookup, str):
        return id_lookup

    data = id_lookup.get("data") or {}
    channel_id = data.get("channel_id") or data.get("id")
    if not channel_id:
        return f"ERROR: could not resolve channel id from {raw!r}; raw response: {json.dumps(data)[:300]}"

    result = _keyapi_get("/youtube/get_channel_description", {"channel_id": channel_id})
    return _summarize(result, f"YOUTUBE CHANNEL: {raw} (channel_id={channel_id})")


# ---------------------------------------------------------------------------
# Facebook
# ---------------------------------------------------------------------------

@tool("Research Facebook Page")
def research_facebook_page(page_url: str) -> str:
    """Look up details for a public Facebook page or profile by URL.

    Returns page name, follower/likes counts, category, and other public profile data.
    Useful for sizing a competitor brand's FB presence.

    Args:
        page_url: Full Facebook URL (e.g., 'https://www.facebook.com/wellwateredwomen').

    Returns: Page data as JSON string, or an error message.
    """
    url = (page_url or "").strip()
    if not url:
        return "ERROR: page_url is required."
    if not url.startswith("http"):
        url = "https://www.facebook.com/" + url.lstrip("/")
    result = _keyapi_get("/facebook/profile_details_url", {"url": url})
    return _summarize(result, f"FACEBOOK PAGE: {url}")


# ---------------------------------------------------------------------------
# Convenience: status / call-count probe (NOT a CrewAI tool)
# ---------------------------------------------------------------------------

def keyapi_status() -> dict[str, Any]:
    """Lightweight observability — used by /admin or /bridge endpoints if needed."""
    return {
        "configured": bool(_get_api_key()),
        "calls_this_process": _call_count,
        "max_calls_per_process": _max_calls(),
        "base_url": KEYAPI_BASE_URL,
    }


KEYAPI_TOOLS = [
    research_tiktok_creator,
    search_tiktok_creators,
    research_instagram_creator,
    search_instagram_creators,
    research_youtube_channel,
    research_facebook_page,
]
