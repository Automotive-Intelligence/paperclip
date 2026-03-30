"""
tools/zernio.py — Zernio Social Media API Integration for Paperclip
Unified multi-platform social posting (14+ networks) via single API.
Supports Twitter/X, Instagram, Facebook, LinkedIn, TikTok, YouTube, Pinterest,
Reddit, Bluesky, Threads, Google Business, Telegram, Snapchat, WhatsApp.
"""

import os
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
import requests
from services.errors import ServiceCallError, ServiceError

# Zernio API Configuration
ZERNIO_BASE_URL = "https://zernio.com/api/v1"
ZERNIO_API_KEY = os.getenv("ZERNIO_API_KEY", "").strip()

# Platform mapping: Paperclip names → Zernio platform identifiers
ZERNIO_PLATFORMS = {
    "twitter": "twitter",
    "x": "twitter",
    "instagram": "instagram",
    "facebook": "facebook",
    "linkedin": "linkedin",
    "tiktok": "tiktok",
    "youtube": "youtube",
    "pinterest": "pinterest",
    "reddit": "reddit",
    "bluesky": "bluesky",
    "threads": "threads",
    "googlebusiness": "googlebusiness",
    "telegram": "telegram",
    "snapchat": "snapchat",
    "whatsapp": "whatsapp",
}

# Platform capabilities (which ones support images, videos, scheduling, etc.)
PLATFORM_CAPABILITIES = {
    "twitter": {"media": True, "video": True, "scheduling": True, "max_length": 280},
    "instagram": {"media": True, "video": True, "scheduling": True, "max_length": 2200},
    "facebook": {"media": True, "video": True, "scheduling": True, "max_length": 63206},
    "linkedin": {"media": True, "video": True, "scheduling": True, "max_length": 3000},
    "tiktok": {"media": False, "video": True, "scheduling": True, "max_length": 2200},
    "youtube": {"media": False, "video": True, "scheduling": False, "max_length": None},
    "pinterest": {"media": True, "video": True, "scheduling": True, "max_length": 500},
    "reddit": {"media": True, "video": True, "scheduling": False, "max_length": 40000},
    "bluesky": {"media": True, "video": False, "scheduling": False, "max_length": 300},
    "threads": {"media": True, "video": False, "scheduling": False, "max_length": 500},
    "googlebusiness": {"media": True, "video": True, "scheduling": True, "max_length": 300},
    "telegram": {"media": True, "video": True, "scheduling": False, "max_length": 4096},
    "snapchat": {"media": True, "video": True, "scheduling": False, "max_length": None},
    "whatsapp": {"media": True, "video": True, "scheduling": False, "max_length": None},
}


def _zernio_service_error(
    operation: str,
    message: str,
    *,
    status_code: Optional[int] = None,
    retryable: bool = False,
    details: Optional[Dict[str, Any]] = None,
) -> ServiceCallError:
    return ServiceCallError(
        ServiceError(
            provider="zernio",
            operation=operation,
            message=message,
            status_code=status_code,
            retryable=retryable,
            details=details,
        )
    )


def zernio_ready() -> bool:
    """Check if Zernio API is configured and ready to use."""
    return bool(ZERNIO_API_KEY)


def _zernio_request(
    method: str,
    endpoint: str,
    data: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    Make authenticated request to Zernio API.
    
    Args:
        method: HTTP method (GET, POST, PUT, DELETE)
        endpoint: API endpoint path (e.g., '/posts')
        data: Request body for POST/PUT requests
        timeout: Request timeout in seconds
        
    Returns:
        JSON response from Zernio API
        
    Raises:
        ServiceCallError: On API failure or authentication error
    """
    operation = f"{method} {endpoint}"

    if not ZERNIO_API_KEY:
        raise _zernio_service_error(
            operation,
            "Zernio API key not configured. Set ZERNIO_API_KEY environment variable.",
        )

    url = f"{ZERNIO_BASE_URL}{endpoint}"
    headers = {
        "Authorization": f"Bearer {ZERNIO_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        if method == "GET":
            response = requests.get(url, headers=headers, timeout=timeout)
        elif method == "POST":
            response = requests.post(url, json=data, headers=headers, timeout=timeout)
        elif method == "PUT":
            response = requests.put(url, json=data, headers=headers, timeout=timeout)
        elif method == "DELETE":
            response = requests.delete(url, headers=headers, timeout=timeout)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        response.raise_for_status()
        return response.json() if response.text else {}

    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code
        error_body = e.response.text
        if status_code == 401:
            raise _zernio_service_error(
                operation,
                "Zernio authentication failed. Check ZERNIO_API_KEY.",
                status_code=status_code,
            )
        elif status_code == 429:
            raise _zernio_service_error(
                operation,
                "Zernio rate limit exceeded. Try again later.",
                status_code=status_code,
                retryable=True,
            )
        else:
            raise _zernio_service_error(
                operation,
                f"Zernio API error ({status_code}): {error_body}",
                status_code=status_code,
                retryable=status_code >= 500,
            )
    except requests.exceptions.RequestException as e:
        raise _zernio_service_error(
            operation,
            f"Zernio API request failed: {e}",
            retryable=True,
        )


# ────────────────────────────────────────────────────────────────────────────
# Media Upload (Presigned)
# ────────────────────────────────────────────────────────────────────────────


def upload_media_to_zernio(
    file_bytes: bytes,
    filename: str,
    mime_type: str = "image/png",
) -> str:
    """
    Upload media to Zernio via presigned URL and return the public CDN URL.

    Flow: POST /media/presign → PUT binary to uploadUrl → return publicUrl

    Args:
        file_bytes: Raw file bytes (PNG, JPEG, MP4, etc.)
        filename: Target filename (e.g. "post-image.png")
        mime_type: MIME type (default "image/png")

    Returns:
        Public URL string ready to use in mediaItems

    Raises:
        ServiceCallError on any failure
    """
    # Step 1: Request presigned upload URL
    presign_resp = _zernio_request(
        "POST",
        "/media/presign",
        {"filename": filename, "contentType": mime_type},
    )

    upload_url = presign_resp.get("uploadUrl")
    public_url = presign_resp.get("publicUrl")

    if not upload_url or not public_url:
        raise _zernio_service_error(
            "media_presign",
            "Zernio presign response missing uploadUrl or publicUrl",
            status_code=200,
            retryable=False,
            details={"response": presign_resp},
        )

    # Step 2: PUT binary directly to presigned URL (no auth header)
    try:
        put_resp = requests.put(
            upload_url,
            data=file_bytes,
            headers={"Content-Type": mime_type},
            timeout=60,
        )
        put_resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise _zernio_service_error(
            "media_upload",
            f"Failed to PUT media to presigned URL: {e}",
            retryable=True,
        )

    logging.info(f"[Zernio] Media uploaded: {public_url} ({len(file_bytes)} bytes)")
    return public_url


# ────────────────────────────────────────────────────────────────────────────
# Profile Management
# ────────────────────────────────────────────────────────────────────────────


def create_zernio_profile(name: str, description: str = "") -> Dict[str, Any]:
    """
    Create a new Zernio profile to group social accounts.
    
    Args:
        name: Profile name (e.g., "The AI Phone Guy", "Calling Digital")
        description: Optional profile description
        
    Returns:
        Profile data with _id field
    """
    data = {
        "name": name,
        "description": description or "",
    }
    result = _zernio_request("POST", "/profiles", data)
    logging.info(f"[Zernio] Created profile: {result.get('_id')} ({name})")
    return result


def get_zernio_profiles() -> List[Dict[str, Any]]:
    """List all Zernio profiles."""
    result = _zernio_request("GET", "/profiles")
    profiles = result.get("profiles", [])
    logging.info(f"[Zernio] Found {len(profiles)} profile(s)")
    return profiles


def get_zernio_profile(profile_id: str) -> Dict[str, Any]:
    """Get details for a specific Zernio profile."""
    result = _zernio_request("GET", f"/profiles/{profile_id}")
    return result


# ────────────────────────────────────────────────────────────────────────────
# Account Management
# ────────────────────────────────────────────────────────────────────────────


def get_zernio_connect_url(profile_id: str, platform: str) -> str:
    """
    Get OAuth authorization URL to connect a social account to Zernio.
    
    Args:
        profile_id: Zernio profile ID
        platform: Platform name (e.g., 'twitter', 'instagram', 'linkedin')
        
    Returns:
        URL to redirect user for OAuth authorization
    """
    if platform.lower() not in ZERNIO_PLATFORMS:
        raise ValueError(f"Unsupported platform: {platform}")

    normalized_platform = ZERNIO_PLATFORMS[platform.lower()]
    data = {
        "platform": normalized_platform,
        "profileId": profile_id,
    }
    result = _zernio_request("POST", "/connect/url", data)
    auth_url = result.get("authUrl")
    if not auth_url:
        raise _zernio_service_error(
            "POST /connect/url",
            f"Failed to get auth URL for {platform}",
        )
    logging.info(f"[Zernio] Generated auth URL for {platform} on profile {profile_id}")
    return auth_url


def list_zernio_accounts(profile_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    List all connected social accounts, optionally filtered by profile.
    
    Args:
        profile_id: Optional profile ID to filter accounts
        
    Returns:
        List of connected account objects with _id, platform, username, etc.
    """
    if profile_id:
        try:
            result = _zernio_request("GET", f"/profiles/{profile_id}/accounts")
        except ServiceCallError as e:
            # Some Zernio API versions do not expose /profiles/{id}/accounts.
            # Fall back to /accounts and filter client-side when possible.
            if e.error.status_code == 404:
                all_accounts = _zernio_request("GET", "/accounts").get("accounts", [])

                def _account_profile_id(account: Dict[str, Any]) -> str:
                    pid = account.get("profileId") or account.get("profile_id")
                    if isinstance(pid, dict):
                        return str(pid.get("_id") or "")
                    return str(pid or "")

                accounts = [
                    a
                    for a in all_accounts
                    if _account_profile_id(a) == str(profile_id)
                ]
                logging.info(
                    f"[Zernio] Found {len(accounts)} connected account(s) "
                    f"for profile {profile_id} via fallback"
                )
                return accounts
            raise
    else:
        result = _zernio_request("GET", "/accounts")

    accounts = result.get("accounts", [])
    logging.info(f"[Zernio] Found {len(accounts)} connected account(s)")
    return accounts


def get_zernio_account(account_id: str) -> Dict[str, Any]:
    """Get details for a specific connected account."""
    result = _zernio_request("GET", f"/accounts/{account_id}")
    return result


def disconnect_zernio_account(account_id: str) -> bool:
    """Disconnect a social account from Zernio."""
    _zernio_request("DELETE", f"/accounts/{account_id}")
    logging.info(f"[Zernio] Disconnected account {account_id}")
    return True


# ────────────────────────────────────────────────────────────────────────────
# Post Publishing
# ────────────────────────────────────────────────────────────────────────────


def publish_to_zernio(
    content: str,
    platforms: List[str],
    account_ids: Optional[List[str]] = None,
    scheduled_for: Optional[str] = None,
    media_urls: Optional[List[str]] = None,
    publish_now: bool = False,
    timezone: str = "America/Chicago",
) -> Dict[str, Any]:
    """
    Publish or schedule a post to one or more social platforms via Zernio.
    
    Args:
        content: Post text/caption
        platforms: List of platform names (e.g., ['twitter', 'linkedin'])
        account_ids: Specific account IDs to post to (optional, uses defaults if not provided)
        scheduled_for: ISO 8601 timestamp for scheduled posting (e.g., '2026-04-01T14:00:00')
        media_urls: Optional list of image/video URLs to attach
        publish_now: If True, publish immediately instead of scheduling
        timezone: Timezone for scheduled_for interpretation
        
    Returns:
        Post object with _id, status, scheduled_for, etc.
        
    Raises:
        ValueError: If content exceeds platform limits or invalid platforms provided
        ServiceCallError: On API failure
    """
    if not content or not content.strip():
        raise ValueError("Content cannot be empty")

    # Normalize platform names
    normalized_platforms = []
    for p in platforms:
        p_lower = p.lower()
        if p_lower not in ZERNIO_PLATFORMS:
            raise ValueError(f"Unsupported platform: {p}")
        normalized_platforms.append(ZERNIO_PLATFORMS[p_lower])

    # Validate content length per platform
    for platform in normalized_platforms:
        caps = PLATFORM_CAPABILITIES.get(platform, {})
        max_len = caps.get("max_length")
        if max_len and len(content) > max_len:
            logging.warning(
                f"[Zernio] Content length ({len(content)}) exceeds "
                f"{platform} limit ({max_len})"
            )

    # Build platform config
    platform_config = []
    if account_ids:
        # Specific accounts provided
        for account_id in account_ids:
            for platform in normalized_platforms:
                platform_config.append({
                    "platform": platform,
                    "accountId": account_id,
                })
    else:
        # Use all connected accounts for these platforms
        try:
            accounts = list_zernio_accounts()
            for account in accounts:
                if account.get("platform") in normalized_platforms:
                    platform_config.append({
                        "platform": account["platform"],
                        "accountId": account["_id"],
                    })
        except Exception as e:
            logging.warning(f"[Zernio] Could not auto-discover accounts: {e}")
            raise _zernio_service_error(
                "publish_to_zernio",
                "No account_ids provided and auto-discovery failed. Pass explicit account_ids.",
                details={"platforms": normalized_platforms},
            )

    if not platform_config:
        raise _zernio_service_error(
            "publish_to_zernio",
            f"No accounts found for platforms: {normalized_platforms}",
            details={"platforms": normalized_platforms},
        )

    # Build request
    post_data = {
        "content": content.strip(),
        "platforms": platform_config,
    }

    if media_urls:
        media_items = []
        for url in media_urls:
            lower = url.lower()
            if any(lower.endswith(ext) for ext in [".mp4", ".mov", ".avi", ".webm"]):
                media_type = "video"
            else:
                media_type = "image"
            media_items.append({"url": url, "type": media_type})
        post_data["mediaItems"] = media_items

    if publish_now:
        post_data["publishNow"] = True
    elif scheduled_for:
        post_data["scheduledFor"] = scheduled_for
        post_data["timezone"] = timezone

    result = _zernio_request("POST", "/posts", post_data)
    
    post_id = result.get("_id")
    status = result.get("status", "unknown")
    logging.info(
        f"[Zernio] Post created: {post_id} (status: {status}, "
        f"platforms: {', '.join(normalized_platforms)})"
    )
    
    return result


def create_zernio_draft(
    content: str,
    platforms: List[str],
    media_urls: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Create a draft post without publishing or scheduling.
    
    Args:
        content: Post text
        platforms: List of platforms to draft for
        media_urls: Optional media URLs
        
    Returns:
        Draft post object
    """
    # Build platform config without account IDs (draft mode)
    platform_config = [
        {"platform": ZERNIO_PLATFORMS[p.lower()]}
        for p in platforms
        if p.lower() in ZERNIO_PLATFORMS
    ]

    post_data = {
        "content": content.strip(),
        "platforms": platform_config,
    }

    if media_urls:
        media_items = []
        for url in media_urls:
            lower = url.lower()
            if any(lower.endswith(ext) for ext in [".mp4", ".mov", ".avi", ".webm"]):
                media_type = "video"
            else:
                media_type = "image"
            media_items.append({"url": url, "type": media_type})
        post_data["mediaItems"] = media_items

    result = _zernio_request("POST", "/posts", post_data)
    logging.info(f"[Zernio] Draft created: {result.get('_id')}")
    return result


def update_zernio_post(
    post_id: str,
    content: Optional[str] = None,
    scheduled_for: Optional[str] = None,
) -> Dict[str, Any]:
    """Update a draft or scheduled post."""
    update_data = {}
    if content:
        update_data["content"] = content
    if scheduled_for:
        update_data["scheduledFor"] = scheduled_for

    result = _zernio_request("PUT", f"/posts/{post_id}", update_data)
    logging.info(f"[Zernio] Post updated: {post_id}")
    return result


def delete_zernio_post(post_id: str) -> bool:
    """Delete a draft or scheduled post."""
    _zernio_request("DELETE", f"/posts/{post_id}")
    logging.info(f"[Zernio] Post deleted: {post_id}")
    return True


def get_zernio_post(post_id: str) -> Dict[str, Any]:
    """Get details for a specific post."""
    return _zernio_request("GET", f"/posts/{post_id}")


def list_zernio_posts(
    profile_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    List posts, optionally filtered by profile or status.
    
    Args:
        profile_id: Optional profile ID to filter
        status: Optional status filter (draft, scheduled, published)
        limit: Max posts to return
        
    Returns:
        List of post objects
    """
    endpoint = "/posts"
    if profile_id:
        endpoint = f"/profiles/{profile_id}/posts"

    result = _zernio_request("GET", f"{endpoint}?limit={limit}")
    posts = result.get("posts", [])

    if status:
        posts = [p for p in posts if p.get("status") == status]

    return posts


# ────────────────────────────────────────────────────────────────────────────
# Analytics
# ────────────────────────────────────────────────────────────────────────────


def get_zernio_analytics(
    post_id: Optional[str] = None,
    profile_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get analytics for posts or profile.
    
    Args:
        post_id: Get analytics for specific post
        profile_id: Get analytics for profile
        start_date: ISO date filter
        end_date: ISO date filter
        
    Returns:
        Analytics data with impressions, engagement, etc.
    """
    if post_id:
        endpoint = f"/analytics/posts/{post_id}"
    elif profile_id:
        endpoint = f"/analytics/profiles/{profile_id}"
    else:
        endpoint = "/analytics"

    params = {}
    if start_date:
        params["startDate"] = start_date
    if end_date:
        params["endDate"] = end_date

    query_string = "&".join([f"{k}={v}" for k, v in params.items()])
    if query_string:
        endpoint = f"{endpoint}?{query_string}"

    result = _zernio_request("GET", endpoint)
    return result


# ────────────────────────────────────────────────────────────────────────────
# Content Agent Helpers
# ────────────────────────────────────────────────────────────────────────────


def publish_content_piece_to_zernio(
    piece: Dict[str, Any],
    profile_id: str,
    publish_now: bool = False,
    scheduled_for: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Publish a parsed content piece (from agents) to Zernio.

    Supports both parser output (`body`) and direct social payload (`content`).

    Expects piece to have:
    - content or body: Post text
    - platform: Single platform name
    - media_url/image_url: Optional media URL
    - scheduled_for: Optional scheduling timestamp

    Args:
        piece: Content piece dict from agent output
        profile_id: Zernio profile ID (business profile)
        publish_now: Publish immediately instead of scheduling
        scheduled_for: Override piece's scheduled_for time
    """
    content = (piece.get("content") or piece.get("body") or "").strip()
    if not content:
        raise ValueError("Content piece must have 'content' or 'body' field")

    platform = piece.get("platform", "").lower()
    if not platform:
        raise ValueError("Content piece must have 'platform' field")

    media_urls = []
    media_url = piece.get("media_url") or piece.get("image_url")
    if media_url:
        media_urls.append(media_url)

    # Determine scheduling
    schedule_time = scheduled_for or piece.get("scheduled_for")

    # Get accounts for this profile and platform
    try:
        accounts = list_zernio_accounts(profile_id)
        target_accounts = [
            a for a in accounts
            if ZERNIO_PLATFORMS.get(platform) == a.get("platform")
        ]
        account_ids = [a["_id"] for a in target_accounts]
    except Exception as e:
        logging.warning(f"[Zernio] Could not discover accounts for profile {profile_id}: {e}")
        account_ids = None

    # Publish
    return publish_to_zernio(
        content=content,
        platforms=[platform],
        account_ids=account_ids,
        scheduled_for=schedule_time,
        media_urls=media_urls if media_urls else None,
        publish_now=publish_now and not schedule_time,
    )
