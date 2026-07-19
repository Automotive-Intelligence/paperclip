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
