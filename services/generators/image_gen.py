"""
services/generators/image_gen.py — provider-swappable image generation.

Sits upstream of Zernio. Produces on-brand stills with a best-of-breed
model and hands the URL downstream. The active provider is chosen by the
IMAGE_GEN_PROVIDER env var, not a code edit.

Providers delegate to the repo's proven image integrations rather than
reimplementing them:
  - "nano_banana" (default) -> tools.image_gen.generate_image_hybrid, the
    kie.ai Google Nano Banana path. Photoreal, on-brand stills.
  - "flux" -> tools.image_gen.generate_image_hybrid, Replicate FLUX. The
    cheap stylized workhorse.

Adding a brand-new provider (e.g. Gemini direct) is a one-line registry
entry plus its runner. Switching among registered providers is env-only.
See services/generators/README.md.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from services.generators.base import Generator, GenerationResult

logger = logging.getLogger(__name__)

DEFAULT_IMAGE_PROVIDER = "nano_banana"


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


class ImageGenerator(Generator):
    """Image generator. Provider chosen by IMAGE_GEN_PROVIDER."""

    media_kind = "image"
    env_var = "IMAGE_GEN_PROVIDER"
    default_provider = DEFAULT_IMAGE_PROVIDER

    def _registry(self) -> Dict[str, Callable[..., GenerationResult]]:
        return {
            "nano_banana": lambda **kw: _run_hybrid("nano_banana", **kw),
            "flux": lambda **kw: _run_hybrid("flux", **kw),
        }

    def ready(self) -> bool:
        try:
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
