"""tools/buffer.py — Buffer GraphQL API wrapper for social scheduling.

Wraps Buffer's modern GraphQL API (https://api.buffer.com/graphql) so Internal
Marketing agents can draft and schedule social posts to client channels under
the Calling Digital team account.

Buffer model:
  - One team account = one BUFFER_API_KEY (Bearer auth)
  - The team has Organizations
  - Organizations have Channels (Instagram, TikTok, Facebook, etc.)
  - Each Channel has id, service (platform), descriptor (handle), name
  - Posts target ONE channel per createPost call (channelId is singular)

For Calling Digital's agency model: all client channels live under one team
plan, segregated logically by business_key. The mapping config in
config/buffer_channels.json defines which Buffer channel IDs belong to which
client. Multi-channel posting = call createPost N times, one per channel.

Auth (env vars):
  BUFFER_API_KEY                Bearer token from buffer.com → Get API Key

Tools exposed:
  - buffer_list_organizations() — orgs + ownerEmail + channelCount
  - buffer_list_channels(business_key) — channels (all, or filtered to one client)
  - buffer_create_draft_post(business_key, text, media_urls, channel_ids) —
        create a DRAFT post (saveToDraft=true). Loops over channel IDs since
        Buffer's createPost takes ONE channelId per call.
  - buffer_list_posts(channel_id, status, limit) — read posts on a channel
  - buffer_delete_post(post_id) — clean up test posts

Errors return as strings (matches keyapi.py / klaviyo.py / shopify.py pattern)
so the LLM gets useful feedback instead of a crashed Crew.

API reference: https://developers.buffer.com
GraphQL endpoint: https://api.buffer.com/graphql
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import requests
from crewai.tools import tool

logger = logging.getLogger(__name__)

BUFFER_GRAPHQL_URL = "https://api.buffer.com/graphql"
DEFAULT_TIMEOUT = 30

# Channel mapping config: { business_key: [channel_id, ...] }
_CHANNEL_MAP_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "buffer_channels.json"
)


def _api_key() -> str | None:
    val = (os.environ.get("BUFFER_API_KEY") or "").strip()
    return val or None


def _channel_map() -> dict[str, list[str]]:
    """Return business_key -> [channel_id, ...] mapping, or empty dict."""
    if not _CHANNEL_MAP_PATH.exists():
        return {}
    try:
        return json.loads(_CHANNEL_MAP_PATH.read_text())
    except json.JSONDecodeError as e:
        logger.warning("buffer_channels.json parse error: %s", e)
        return {}


def _buffer_query(
    query: str, variables: dict[str, Any] | None = None
) -> dict[str, Any] | str:
    """Low-level GraphQL POST to Buffer's API.

    Returns parsed data dict on success, or error string on failure.
    """
    key = _api_key()
    if not key:
        return "ERROR: BUFFER_API_KEY env var not set."

    body: dict[str, Any] = {"query": query}
    if variables is not None:
        body["variables"] = variables

    try:
        resp = requests.post(
            BUFFER_GRAPHQL_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=body,
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.exceptions.Timeout:
        return f"ERROR: Buffer timeout (>{DEFAULT_TIMEOUT}s)"
    except requests.exceptions.RequestException as e:
        return f"ERROR: Buffer request failed: {type(e).__name__}: {e}"

    if resp.status_code != 200:
        return f"ERROR: Buffer HTTP {resp.status_code}: {resp.text[:300]}"

    try:
        result = resp.json()
    except ValueError:
        return f"ERROR: Buffer returned non-JSON: {resp.text[:300]}"

    if "errors" in result:
        msgs = "; ".join(e.get("message", "?") for e in result["errors"])
        return f"ERROR: Buffer GraphQL: {msgs}"

    return result.get("data", {})


# ----------------------------------------------------------------------
# Read tools
# ----------------------------------------------------------------------

@tool
def buffer_list_organizations() -> str:
    """List all Buffer organizations under the team account.

    Returns a JSON-encoded list with id, name, channelCount, ownerEmail.
    Useful for confirming team account state and finding which org owns
    specific channels.
    """
    q = """{
      account {
        email
        organizations {
          id
          name
          channelCount
          ownerEmail
        }
      }
    }"""
    data = _buffer_query(q)
    if isinstance(data, str):
        return data
    orgs = data.get("account", {}).get("organizations", [])
    return json.dumps(orgs, indent=2)


@tool
def buffer_list_channels(business_key: str = "") -> str:
    """List Buffer channels (social accounts).

    If business_key is non-empty AND found in config/buffer_channels.json,
    returns only channels mapped to that business_key. Otherwise returns ALL
    channels under the team account (so the caller can identify them and
    populate the mapping).

    Each channel includes: id, name, displayName, descriptor (handle),
    service (platform), isDisconnected, isLocked, organizationId.
    """
    q = """{
      account {
        organizations {
          id
          name
          channels {
            id
            name
            displayName
            descriptor
            service
            isDisconnected
            isLocked
            organizationId
          }
        }
      }
    }"""
    data = _buffer_query(q)
    if isinstance(data, str):
        return data

    all_channels: list[dict[str, Any]] = []
    for org in data.get("account", {}).get("organizations", []):
        all_channels.extend(org.get("channels", []) or [])

    if business_key:
        mapping = _channel_map().get(business_key, [])
        if mapping:
            all_channels = [c for c in all_channels if c.get("id") in mapping]
        # else: no mapping configured -> return all so caller can configure

    return json.dumps(all_channels, indent=2)


def _lookup_org_for_channel(channel_id: str) -> str | None:
    """Resolve the organizationId that owns a given channel.

    Buffer's modern `posts(input: PostsInput!)` query requires an organizationId
    inside the input, but the wrapper API only takes channel_id. This walks the
    account → organizations → channels graph to find the owning org.

    Returns the org id string, or None if not found (channel not visible to
    this API key, or channel id is bogus).
    """
    q = """{
      account {
        organizations {
          id
          channels { id }
        }
      }
    }"""
    data = _buffer_query(q)
    if isinstance(data, str):
        return None
    for org in data.get("account", {}).get("organizations", []) or []:
        for ch in org.get("channels", []) or []:
            if ch.get("id") == channel_id:
                return org.get("id")
    return None


@tool
def buffer_list_posts(channel_id: str, status: str = "draft", limit: int = 10) -> str:
    """List posts on a Buffer channel.

    channel_id: the Buffer channel ID to query.
    status:     'draft', 'needs_approval', 'scheduled', 'sending', 'sent', 'error'.
    limit:      max posts to return (default 10).

    Uses Buffer's `posts(input: PostsInput!, first: Int)` query shape (migrated
    from the older positional-args signature, which Buffer removed). Looks up
    the channel's organizationId internally so the caller API stays stable.
    """
    org_id = _lookup_org_for_channel(channel_id)
    if not org_id:
        return (
            f"ERROR: channel {channel_id!r} not found under any organization "
            "this BUFFER_API_KEY has access to."
        )

    q = """query ListPosts($input: PostsInput!, $first: Int) {
      posts(input: $input, first: $first) {
        edges {
          node {
            id
            status
            text
            dueAt
            createdAt
            channel { id name service descriptor }
          }
        }
        pageInfo { hasNextPage }
      }
    }"""
    variables = {
        "input": {
            "organizationId": org_id,
            "filter": {
                "channelIds": [channel_id],
                "status": [status.lower()],
            },
        },
        "first": int(limit),
    }
    data = _buffer_query(q, variables)
    if isinstance(data, str):
        return data
    return json.dumps(data, indent=2)


@tool
def buffer_list_ideas(org_id: str, limit: int = 10) -> str:
    """List Ideas in Buffer's Ideas inbox for an organization.

    org_id: Buffer organization ID (see buffer_list_organizations() output).
    limit:  max ideas to return (default 10, required by Buffer's API).

    NOTE: As of 2026-06-13, Buffer's Ideas API endpoint requires elevated
    plan-tier access (or a token scope) that the current BUFFER_API_KEY does
    NOT have. The wrapper returns a clean error string in that case
    ("ERROR: Buffer GraphQL: Not authorized to access this resource") so the
    caller can detect and surface to the user.

    When the auth scope is resolved, this returns a JSON-encoded payload with
    the Ideas inbox edges, matching Buffer's PaginatedIdeasList shape.
    """
    q = """query ListIdeas($input: IdeasListInput!) {
      ideas(input: $input) {
        __typename
        ... on PaginatedIdeasList {
          edges {
            node {
              id
              content { title text aiAssisted }
            }
          }
          pageInfo { hasNextPage }
        }
      }
    }"""
    variables = {
        "input": {
            "organizationId": org_id,
            "first": int(limit),
        },
    }
    data = _buffer_query(q, variables)
    if isinstance(data, str):
        return data
    return json.dumps(data, indent=2)


# ----------------------------------------------------------------------
# Write tools
# ----------------------------------------------------------------------

@tool
def buffer_create_draft_post(
    business_key: str,
    text: str,
    media_urls: str = "",
    channel_ids: str = "",
) -> str:
    """Create a DRAFT post in Buffer for one or more channels.

    Posts are created with saveToDraft=true, so they sit in the user's drafts
    section. Nothing publishes until the user explicitly schedules or sends
    from the Buffer UI. This is the safe smoke-test path.

    Buffer's createPost mutation takes ONE channelId per call, so this fn
    loops over the target channels and creates one draft per channel.

    Args:
        business_key: which client this post is for (e.g. 'paperandpurpose').
            Used to look up channel_ids from config/buffer_channels.json if
            channel_ids is empty.
        text:        the post copy.
        media_urls:  comma-separated list of public image/video URLs to
                     attach. Each becomes an asset on the post.
        channel_ids: comma-separated list of channel IDs to post to. If
                     empty, uses the business_key's configured channels.

    Returns a JSON-encoded list of {channel_id, post_id|error} per channel.
    """
    target_ids = [c.strip() for c in channel_ids.split(",") if c.strip()]
    if not target_ids:
        target_ids = _channel_map().get(business_key, [])
    if not target_ids:
        return (
            f"ERROR: no target channels for business_key={business_key!r}. "
            f"Populate config/buffer_channels.json or pass channel_ids."
        )

    media_list = [m.strip() for m in media_urls.split(",") if m.strip()]
    assets = [{"url": url} for url in media_list]

    mutation = """mutation CreateDraft($input: CreatePostInput!) {
      createPost(input: $input) {
        id
        status
        text
        channelId
        channel { name service }
      }
    }"""

    results: list[dict[str, Any]] = []
    for channel_id in target_ids:
        input_obj = {
            "channelId": channel_id,
            "text": text,
            "schedulingType": "notification",
            "mode": "addToQueue",
            "assets": assets,
            "saveToDraft": True,
            "source": "paperclip",
        }
        data = _buffer_query(mutation, {"input": input_obj})
        if isinstance(data, str):
            results.append({"channel_id": channel_id, "error": data})
        else:
            results.append(
                {"channel_id": channel_id, "post": data.get("createPost", {})}
            )

    return json.dumps(results, indent=2)


@tool
def buffer_delete_post(post_id: str) -> str:
    """Delete a Buffer post by id. Use to clean up smoke-test drafts."""
    mutation = """mutation DeletePost($input: DeletePostInput!) {
      deletePost(input: $input) {
        success
      }
    }"""
    data = _buffer_query(mutation, {"input": {"id": post_id}})
    if isinstance(data, str):
        return data
    return json.dumps(data, indent=2)


# Tool registry for CrewAI agents
BUFFER_TOOLS = [
    buffer_list_organizations,
    buffer_list_channels,
    buffer_list_posts,
    buffer_list_ideas,
    buffer_create_draft_post,
    buffer_delete_post,
]
