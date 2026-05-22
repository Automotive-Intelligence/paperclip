"""
services/generators/video_gen.py — provider-swappable video generation.

Image-to-video off our own assets: pass source_image_url as the first
frame. Sits upstream of Zernio; produces the clip, hands the URL down.
The active provider is chosen by the VIDEO_GEN_PROVIDER env var.

Providers delegate to the repo's proven video integrations:
  - "kling" (default) -> tools.video_gen.generate_video_routed, Replicate
    Kling. Cheap, fast short-form B-roll.
  - "seedance_pro" -> the fal.ai ByteDance Seedance Pro path. Premium
    cinematic B-roll where motion quality matters.
  - "veo" -> placeholder. Selecting it returns a clean "not wired" error
    rather than crashing; it shows the shape of adding a new provider.

See services/generators/README.md.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from services.generators.base import Generator, GenerationResult

logger = logging.getLogger(__name__)

DEFAULT_VIDEO_PROVIDER = "kling"


def _run_routed(model: str, *, prompt: str, business_key: str, platform: str,
                aspect_ratio: str, source_image_url: Optional[str],
                **kwargs: Any) -> GenerationResult:
    """Delegate to tools.video_gen.generate_video_routed.

    source_image_url maps to the routed dispatcher's start_image_url — this
    is how we do image-to-video off our own assets.
    """
    from tools.video_gen import generate_video_routed

    duration = kwargs.get("duration", 0)
    try:
        duration = int(duration) if duration else 0
    except (TypeError, ValueError):
        duration = 0

    result = generate_video_routed(
        prompt=prompt,
        business_key=business_key,
        platform=platform,
        model=model,
        duration=duration,
        start_image_url=source_image_url or None,
    )
    if isinstance(result, str):
        return GenerationResult(
            ok=False, media_kind="video", provider=model, error=result,
        )
    url = result.get("url")
    urls = result.get("urls") or ([url] if url else [])
    meta = {k: v for k, v in result.items() if k not in ("urls", "url")}
    if source_image_url:
        meta["source_image_url"] = source_image_url
        meta["mode"] = "image-to-video"
    return GenerationResult(
        ok=bool(urls), media_kind="video", provider=model, urls=list(urls),
        error="" if urls else "generation returned no url", meta=meta,
    )


def _veo_not_wired(**kwargs: Any) -> GenerationResult:
    return GenerationResult(
        ok=False, media_kind="video", provider="veo",
        error=(
            "veo provider is not wired yet. Add a Veo runner to the registry "
            "in services/generators/video_gen.py and a key check in ready(). "
            "See services/generators/README.md."
        ),
    )


class VideoGenerator(Generator):
    """Video generator. Provider chosen by VIDEO_GEN_PROVIDER."""

    media_kind = "video"
    env_var = "VIDEO_GEN_PROVIDER"
    default_provider = DEFAULT_VIDEO_PROVIDER

    def _registry(self) -> Dict[str, Callable[..., GenerationResult]]:
        return {
            "kling": lambda **kw: _run_routed("kling", **kw),
            "seedance_pro": lambda **kw: _run_routed("seedance_pro", **kw),
            "veo": _veo_not_wired,
        }

    def ready(self) -> bool:
        try:
            if self._provider == "kling":
                from tools.video_gen import video_gen_ready
                return video_gen_ready()
            if self._provider == "seedance_pro":
                from tools.fal_ai import fal_ai_ready
                return fal_ai_ready()
            if self._provider == "veo":
                return False
        except Exception as e:
            logger.warning("[VideoGenerator] readiness check failed: %s", e)
        return False


def get_video_generator(provider: Optional[str] = None) -> VideoGenerator:
    """Factory. Pass `provider` to override VIDEO_GEN_PROVIDER for one call."""
    return VideoGenerator(provider=provider)
