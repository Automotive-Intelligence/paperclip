"""
services/generators/image_gen.py — provider-swappable image generation.

Sits upstream of Zernio. Produces on-brand stills with a best-of-breed
model and hands the URL downstream. The active provider is chosen by the
IMAGE_GEN_PROVIDER env var, not a code edit.

Providers delegate to the repo's proven image integrations rather than
reimplementing them:
  - "nano_banana_2" (default) -> tools.fal_image, Google Nano Banana (Gemini
    Flash Image) DIRECT via fal.ai. Current model, cheap workhorse. Replaces
    the stale kie.ai path. Supports image-to-image from a brand reference.
  - "nano_banana_pro" -> tools.fal_image, Nano Banana Pro (Gemini 3 Pro Image)
    via fal.ai. Hero/finals; up to 14 reference images, 4K, localized edits.
  - "nano_banana" (legacy fallback) -> tools.image_gen.generate_image_hybrid,
    the OLD kie.ai Nano Banana path. Kept only as a fallback.
  - "flux" -> tools.image_gen.generate_image_hybrid, Replicate FLUX.

Prompt-economy two-tier: prototype on nano_banana_2 (Flash), finalize hero
shots on nano_banana_pro. Switching providers is env-only (IMAGE_GEN_PROVIDER).
See services/generators/README.md.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from services.generators.base import Generator, GenerationResult

logger = logging.getLogger(__name__)

# Default upgraded 2026-06-28: off the stale kie.ai "nano_banana" onto the
# current Nano Banana via fal.ai direct (Visual Gen Phase 0, file 93).
DEFAULT_IMAGE_PROVIDER = "nano_banana_2"


def _run_hybrid(model: str, *, prompt: str, business_key: str, platform: str,
                aspect_ratio: str, source_image_url: Optional[str],
                **kwargs: Any) -> GenerationResult:
    """Delegate to the existing tools.image_gen.generate_image_hybrid dispatcher.

    generate_image_hybrid returns a dict on success or an error string on
    failure (the established repo pattern). source_image_url is recorded in
    meta: the hybrid dispatcher is text-to-image, so edit-off-asset routing
    is a provider-level follow-up, tracked in the README.
    """
    from tools.image_gen import generate_image_hybrid

    result = generate_image_hybrid(
        prompt=prompt,
        business_key=business_key,
        platform=platform,
        aspect_ratio=aspect_ratio,
        model=model,
    )
    if isinstance(result, str):
        return GenerationResult(
            ok=False, media_kind="image", provider=model, error=result,
        )
    urls = result.get("urls") or ([result["url"]] if result.get("url") else [])
    meta = {k: v for k, v in result.items() if k not in ("urls", "url")}
    if source_image_url:
        meta["source_image_url"] = source_image_url
        meta["note"] = "source image recorded; hybrid dispatcher runs text-to-image"
    return GenerationResult(
        ok=bool(urls), media_kind="image", provider=model, urls=list(urls),
        error="" if urls else "generation returned no urls", meta=meta,
    )


def _run_fal(*, pro: bool, prompt: str, business_key: str = "", platform: str = "default",
             aspect_ratio: str = "", source_image_url: Optional[str] = None,
             **kwargs: Any) -> GenerationResult:
    """Run fal.ai Nano Banana (Flash) or Pro via tools.fal_image.

    Returns a dict on success or error string (repo pattern); image-to-image
    is engaged when source_image_url (a brand reference) is provided.
    """
    from tools.fal_image import generate_nano_banana_image

    provider = "nano_banana_pro" if pro else "nano_banana_2"
    result = generate_nano_banana_image(
        prompt=prompt, business_key=business_key, platform=platform,
        aspect_ratio=aspect_ratio, source_image_url=source_image_url, pro=pro,
    )
    if isinstance(result, str):
        return GenerationResult(
            ok=False, media_kind="image", provider=provider, error=result,
        )
    urls = result.get("urls") or []
    meta = {k: v for k, v in result.items() if k != "urls"}
    return GenerationResult(
        ok=bool(urls), media_kind="image", provider=provider, urls=list(urls),
        error="" if urls else "generation returned no urls", meta=meta,
    )


class ImageGenerator(Generator):
    """Image generator. Provider chosen by IMAGE_GEN_PROVIDER."""

    media_kind = "image"
    env_var = "IMAGE_GEN_PROVIDER"
    default_provider = DEFAULT_IMAGE_PROVIDER

    def _registry(self) -> Dict[str, Callable[..., GenerationResult]]:
        return {
            "nano_banana_2": lambda **kw: _run_fal(pro=False, **kw),
            "nano_banana_pro": lambda **kw: _run_fal(pro=True, **kw),
            "nano_banana": lambda **kw: _run_hybrid("nano_banana", **kw),
            "flux": lambda **kw: _run_hybrid("flux", **kw),
        }

    def ready(self) -> bool:
        try:
            if self._provider in ("nano_banana_2", "nano_banana_pro"):
                from tools.fal_image import fal_image_ready
                return fal_image_ready()
            if self._provider == "nano_banana":
                from tools.kie_ai import nano_banana_ready
                return nano_banana_ready()
            if self._provider == "flux":
                from tools.image_gen import image_gen_ready
                return image_gen_ready()
        except Exception as e:
            logger.warning("[ImageGenerator] readiness check failed: %s", e)
        return False


def get_image_generator(provider: Optional[str] = None) -> ImageGenerator:
    """Factory. Pass `provider` to override IMAGE_GEN_PROVIDER for one call."""
    return ImageGenerator(provider=provider)
