from unittest import mock

from services import slipstream_images as si

PROMPTS = [
    {"name": "hero", "prompt": "a cinematic diagram"},
    {"name": "gap", "prompt": "a gap"},
    {"name": "flow", "prompt": "a flow"},
]


def test_generate_images_returns_bytes_per_prompt():
    def _fake_fetch(prompt, business_key, aspect_ratio="", pro=False):
        return {"ok": True, "urls": [f"https://fal/{prompt[:3]}.png"]}

    def _fake_download(url):
        return b"PNGDATA:" + url.encode()

    imgs = si.generate_images(PROMPTS, "autointelligence", fetch=_fake_fetch, download=_fake_download)
    assert set(imgs.keys()) == {"hero", "gap", "flow"}
    assert imgs["hero"].startswith(b"PNGDATA:")


def test_hero_failure_raises():
    def _fetch_hero_fails(prompt, business_key, aspect_ratio="", pro=False):
        return {"ok": False, "error": "fal error"}

    try:
        si.generate_images(PROMPTS, "autointelligence", fetch=_fetch_hero_fails, download=lambda u: b"")
        assert False, "expected ImageError on hero failure"
    except si.ImageError as e:
        assert "hero" in str(e).lower()


def test_blog_hero_renders_on_pro_tier():
    # Guardrail: the BLOG hero doubles as the OG/share image, so it stays on Pro.
    # In-body art does NOT -- it is supporting diagram/screenshot work and ~70% of
    # the volume, so it runs on Flash (~4x cheaper). Tier follows VISIBILITY.
    # If the hero ever passes pro=False, blog share-images silently downgrade; if
    # in-body ever passes pro=True, we are back to paying the most for the images
    # that are seen least.
    seen = {}

    def _fake_fetch(prompt, business_key, aspect_ratio="", pro=False):
        seen[prompt[:3]] = pro
        return {"ok": True, "urls": [f"https://fal/{prompt[:3]}.png"]}

    si.generate_images(PROMPTS, "autointelligence", fetch=_fake_fetch, download=lambda u: b"D")
    tiers = [(spec["name"], seen[spec["prompt"][:3]]) for spec in PROMPTS]
    assert ("hero", True) in tiers                       # hero: Pro, always
    assert all(pro is False for name, pro in tiers if name != "hero")   # in-body: Flash


def test_nonhero_failure_is_skipped_not_fatal():
    def _fetch(prompt, business_key, aspect_ratio="", pro=False):
        if "gap" in prompt or prompt == "a gap":
            return {"ok": False, "error": "x"}
        return {"ok": True, "urls": ["https://fal/x.png"]}

    imgs = si.generate_images(PROMPTS, "autointelligence", fetch=_fetch, download=lambda u: b"D")
    # hero + flow succeed; gap skipped -> still >=2 images (hero + 1)
    assert "hero" in imgs
    assert "gap" not in imgs
    assert len(imgs) >= 2
