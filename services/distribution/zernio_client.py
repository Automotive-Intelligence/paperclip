"""
services/distribution/zernio_client.py — Zernio is the distribution node.

Wraps the official Zernio Python SDK (`zernio-sdk`, `from zernio import
Zernio`). Generation happens upstream (services/generators); Zernio only
posts, schedules, reports analytics, and handles replies across 14+
platforms.

This supersedes the older hand-rolled tools/zernio.py HTTP wrapper.

Design points:
  - One normalized internal `Post` schema. Per-platform copy overrides are
    supported via PlatformTarget.override_content.
  - Idempotent: every Post carries a dedupe_key. create_post claims the key
    in the marketing_post_dedupe table before calling Zernio, so a retry
    never double-posts. A failed Zernio call releases the key so a genuine
    retry can re-attempt.
  - The SDK is imported lazily so this module loads even before zernio-sdk
    is installed (Railway installs it from requirements on deploy).
  - Bearer-token auth: the SDK reads ZERNIO_API_KEY from the environment.

This client PUBLISHES. The human approval gate lives upstream of it
(tools/marketing_tools.py queues for approval; only an approved artifact
reaches create_post with publish_now=True).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ZERNIO_API_KEY = os.getenv("ZERNIO_API_KEY", "").strip()

# Paperclip platform names -> Zernio platform identifiers.
ZERNIO_PLATFORMS = {
    "twitter": "twitter", "x": "twitter", "instagram": "instagram",
    "facebook": "facebook", "linkedin": "linkedin", "tiktok": "tiktok",
    "youtube": "youtube", "pinterest": "pinterest", "reddit": "reddit",
    "bluesky": "bluesky", "threads": "threads",
    "googlebusiness": "googlebusiness", "google_business": "googlebusiness",
    "telegram": "telegram", "snapchat": "snapchat", "whatsapp": "whatsapp",
}


class ZernioError(RuntimeError):
    """Raised on any Zernio distribution failure. Callers (the CrewAI tool
    wrappers, the webhook) catch this and convert to a clean message."""


# ---------------------------------------------------------------------------
# Normalized internal schema
# ---------------------------------------------------------------------------

@dataclass
class PlatformTarget:
    """One platform destination for a post.

    override_content lets a post carry per-platform copy (e.g. a short X
    variant vs a long LinkedIn variant) without separate Post objects.
    """
    platform: str
    account_id: str
    override_content: Optional[str] = None

    def to_sdk(self, default_content: str) -> Dict[str, Any]:
        canonical = ZERNIO_PLATFORMS.get(self.platform.strip().lower())
        if canonical is None:
            raise ZernioError(f"unsupported platform: {self.platform!r}")
        obj: Dict[str, Any] = {"platform": canonical, "accountId": self.account_id}
        if self.override_content and self.override_content.strip():
            obj["platformSpecificContent"] = self.override_content.strip()
        return obj


@dataclass
class Post:
    """Normalized internal post. dedupe_key makes posting idempotent."""
    content: str
    targets: List[PlatformTarget]
    dedupe_key: str
    media_urls: List[str] = field(default_factory=list)
    scheduled_for: Optional[str] = None    # ISO 8601, e.g. 2026-06-01T10:00:00Z
    subject: str = ""                      # internal title, not posted

    def validate(self) -> None:
        if not (self.content or "").strip():
            raise ZernioError("post content is required")
        if not self.targets:
            raise ZernioError("post needs at least one platform target")
        if not (self.dedupe_key or "").strip():
            raise ZernioError("post dedupe_key is required for idempotency")


# ---------------------------------------------------------------------------
# Idempotency table
# ---------------------------------------------------------------------------

_DEDUPE_DDL = """
CREATE TABLE IF NOT EXISTS marketing_post_dedupe (
    dedupe_key     TEXT PRIMARY KEY,
    zernio_post_id TEXT,
    status         TEXT NOT NULL DEFAULT 'claimed',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _ensure_dedupe_table() -> None:
    from services.database import execute_query
    try:
        execute_query(_DEDUPE_DDL, ())
    except Exception as e:  # never let table setup crash a post
        logger.warning("[ZernioClient] dedupe table ensure failed: %s", e)


def _claim_dedupe_key(dedupe_key: str) -> bool:
    """Atomically claim a dedupe key. True = newly claimed (proceed).
    False = already claimed (a retry/dup — do not post again)."""
    from services.database import fetch_all
    _ensure_dedupe_table()
    rows = fetch_all(
        "INSERT INTO marketing_post_dedupe (dedupe_key) VALUES (%s) "
        "ON CONFLICT (dedupe_key) DO NOTHING RETURNING dedupe_key",
        (dedupe_key,),
    )
    return bool(rows)


def _release_dedupe_key(dedupe_key: str) -> None:
    """Release a key after a failed post so a genuine retry can re-attempt."""
    from services.database import execute_query
    try:
        execute_query("DELETE FROM marketing_post_dedupe WHERE dedupe_key = %s",
                      (dedupe_key,))
    except Exception as e:
        logger.warning("[ZernioClient] dedupe release failed for %s: %s",
                       dedupe_key, e)


def _record_dedupe_published(dedupe_key: str, zernio_post_id: str) -> None:
    from services.database import execute_query
    try:
        execute_query(
            "UPDATE marketing_post_dedupe SET zernio_post_id = %s, "
            "status = 'published' WHERE dedupe_key = %s",
            (zernio_post_id, dedupe_key),
        )
    except Exception as e:
        logger.warning("[ZernioClient] dedupe publish-record failed: %s", e)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class ZernioClient:
    """Wrapper over the Zernio SDK. Normalizes our Post schema to the SDK."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = (api_key if api_key is not None else ZERNIO_API_KEY).strip()
        self._sdk: Any = None

    def ready(self) -> bool:
        return bool(self.api_key)

    def _client(self) -> Any:
        """Lazily build and cache the SDK client. Lazy so this module
        imports fine before zernio-sdk is installed."""
        if self._sdk is not None:
            return self._sdk
        if not self.api_key:
            raise ZernioError("ZERNIO_API_KEY not set")
        try:
            from zernio import Zernio
        except ImportError as e:
            raise ZernioError(
                "zernio-sdk not installed. Add `zernio-sdk` to requirements.txt."
            ) from e
        self._sdk = Zernio(api_key=self.api_key)
        return self._sdk

    # -- accounts ------------------------------------------------------------

    def list_accounts(self) -> List[Dict[str, Any]]:
        """Connected social accounts. Used to resolve platform -> accountId."""
        try:
            data = self._client().accounts.list()
        except ZernioError:
            raise
        except Exception as e:
            raise ZernioError(f"list_accounts failed: {type(e).__name__}: {e}") from e
        accounts = data.get("accounts", data) if isinstance(data, dict) else data
        return list(accounts or [])

    # -- posting -------------------------------------------------------------

    def create_post(self, post: Post, publish_now: bool = True) -> Dict[str, Any]:
        """Create a post on Zernio. Idempotent via post.dedupe_key.

        publish_now=True publishes immediately. With post.scheduled_for set,
        it is scheduled instead. A retry with the same dedupe_key is a no-op
        that returns {"deduped": True}.
        """
        post.validate()

        if not _claim_dedupe_key(post.dedupe_key):
            logger.info("[ZernioClient] dedupe hit for %s — skipping repost",
                        post.dedupe_key)
            return {"ok": True, "deduped": True, "skipped": True,
                    "dedupe_key": post.dedupe_key}

        try:
            platforms = [t.to_sdk(post.content) for t in post.targets]
            kwargs: Dict[str, Any] = {
                "content": post.content,
                "platforms": platforms,
            }
            if post.media_urls:
                kwargs["media_urls"] = list(post.media_urls)
            if post.scheduled_for:
                kwargs["scheduled_for"] = post.scheduled_for
            else:
                kwargs["publish_now"] = bool(publish_now)

            result = self._client().posts.create(**kwargs)
            post_id = ""
            if isinstance(result, dict):
                post_id = str(result.get("id") or result.get("postId") or "")
            _record_dedupe_published(post.dedupe_key, post_id)
            return {
                "ok": True, "deduped": False, "zernio_post_id": post_id,
                "scheduled": bool(post.scheduled_for), "dedupe_key": post.dedupe_key,
                "raw": result if isinstance(result, dict) else {},
            }
        except Exception as e:
            # The post did not go out — release the key so a retry can work.
            _release_dedupe_key(post.dedupe_key)
            if isinstance(e, ZernioError):
                raise
            raise ZernioError(f"create_post failed: {type(e).__name__}: {e}") from e

    def schedule_post(self, post: Post) -> Dict[str, Any]:
        """Schedule a post. post.scheduled_for (ISO 8601) is required."""
        if not post.scheduled_for:
            raise ZernioError("schedule_post requires post.scheduled_for (ISO 8601)")
        return self.create_post(post, publish_now=False)

    # -- analytics -----------------------------------------------------------

    def get_analytics(self, period: str = "30d") -> Dict[str, Any]:
        """Cross-platform analytics for the given period (e.g. '7d', '30d')."""
        try:
            data = self._client().analytics.get(period=period)
        except ZernioError:
            raise
        except Exception as e:
            raise ZernioError(f"get_analytics failed: {type(e).__name__}: {e}") from e
        return data if isinstance(data, dict) else {"data": data}

    # -- webhooks ------------------------------------------------------------

    def configure_webhook(self, callback_url: str,
                          events: Optional[List[str]] = None) -> Dict[str, Any]:
        """Register our callback URL so Zernio pushes published/failed events
        instead of us polling. Point this at /webhooks/zernio."""
        events = events or ["post.published", "post.failed"]
        try:
            settings = self._client().webhooks.create_webhook_settings(
                url=callback_url, events=events,
            )
        except ZernioError:
            raise
        except Exception as e:
            raise ZernioError(
                f"configure_webhook failed: {type(e).__name__}: {e}"
            ) from e
        return settings if isinstance(settings, dict) else {"settings": settings}


_DEFAULT_CLIENT: Optional[ZernioClient] = None


def get_zernio_client() -> ZernioClient:
    """Process-wide ZernioClient singleton."""
    global _DEFAULT_CLIENT
    if _DEFAULT_CLIENT is None:
        _DEFAULT_CLIENT = ZernioClient()
    return _DEFAULT_CLIENT
