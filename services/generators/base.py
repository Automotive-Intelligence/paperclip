"""
services/generators/base.py — abstract Generator interface.

The generation layer sits UPSTREAM of Zernio. Zernio's native image output
is weak; we generate on-brand stills and image-to-video off our own assets
with best-of-breed models, then hand the finished media URL to Zernio for
distribution only.

Every concrete generator (image, video) implements one signature so models
are hot-swappable. The active provider is chosen by an env var, not a code
edit — see services/generators/README.md.

A generator NEVER posts anything. It returns media URLs. Distribution and
the human approval gate live downstream (services/distribution + the
approval queue).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class GenerationResult:
    """Uniform return shape for any generator, image or video."""
    ok: bool
    media_kind: str                       # "image" | "video"
    provider: str = ""                    # the provider that ran
    urls: List[str] = field(default_factory=list)
    error: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "media_kind": self.media_kind,
            "provider": self.provider,
            "urls": self.urls,
            "error": self.error,
            "meta": self.meta,
        }


class Generator(ABC):
    """Abstract media generator. One signature, swappable providers.

    A concrete generator holds a registry of provider-name -> callable and
    selects the active one from an env var. `generate()` is the single entry
    point; `source_image_url` is how we generate off our own assets (an edit
    base for images, a first frame for image-to-video).
    """

    media_kind: str = "media"             # overridden by subclasses
    env_var: str = ""                     # env var naming the active provider
    default_provider: str = ""

    def __init__(self, provider: Optional[str] = None) -> None:
        self._provider = (provider or "").strip().lower() or self._provider_from_env()

    # -- provider selection --------------------------------------------------

    def _provider_from_env(self) -> str:
        import os
        return (os.getenv(self.env_var, "").strip().lower() or self.default_provider)

    @property
    def provider(self) -> str:
        return self._provider

    @abstractmethod
    def _registry(self) -> Dict[str, Callable[..., GenerationResult]]:
        """provider-name -> a callable that runs that provider and returns a
        GenerationResult. Adding a brand-new provider is a one-line registry
        entry; switching among registered providers is env-var-only."""

    def available_providers(self) -> List[str]:
        return sorted(self._registry().keys())

    # -- generation ----------------------------------------------------------

    @abstractmethod
    def ready(self) -> bool:
        """True if the active provider's credentials/config are present."""

    def generate(
        self,
        prompt: str,
        *,
        business_key: str = "",
        platform: str = "default",
        aspect_ratio: str = "",
        source_image_url: Optional[str] = None,
        **kwargs: Any,
    ) -> GenerationResult:
        """Generate media with the active provider.

        source_image_url generates off our own assets: an edit base for
        images, the first frame for image-to-video.
        """
        prompt = (prompt or "").strip()
        if not prompt:
            return GenerationResult(
                ok=False, media_kind=self.media_kind, provider=self._provider,
                error="prompt is required",
            )
        registry = self._registry()
        runner = registry.get(self._provider)
        if runner is None:
            return GenerationResult(
                ok=False, media_kind=self.media_kind, provider=self._provider,
                error=(
                    f"unknown {self.media_kind} provider {self._provider!r}. "
                    f"Set {self.env_var} to one of: {', '.join(self.available_providers())}"
                ),
            )
        try:
            return runner(
                prompt=prompt,
                business_key=business_key or "",
                platform=platform or "default",
                aspect_ratio=aspect_ratio or "",
                source_image_url=source_image_url,
                **kwargs,
            )
        except Exception as e:  # a provider must never crash the caller
            return GenerationResult(
                ok=False, media_kind=self.media_kind, provider=self._provider,
                error=f"{type(e).__name__}: {e}",
            )
