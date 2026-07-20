"""Generate: structured parse + validation (mock the LLM seam, never hit the wire)."""
from unittest import mock

import pytest

from services import studio_social_generate as G

CFG = {"display_name": "Automotive Intelligence", "business_key": "autointelligence",
       "voice": "diagnostic", "themes_note": "dealer value",
       "platforms": ["linkedin", "x"]}


def _ok_batch():
    return {"posts": [
        {"key": "p1", "theme": "lead response", "image_prompt": "abstract data scene",
         "platforms": {"linkedin": "LI copy", "x": "X copy"}},
    ]}


def test_generate_returns_clean_posts():
    with mock.patch.object(G, "llm_json", return_value=_ok_batch()):
        posts = G.generate_posts(CFG, "2026-07-27", 1)
    assert len(posts) == 1
    assert posts[0]["platforms"] == {"linkedin": "LI copy", "x": "X copy"}
    assert posts[0]["image_prompt"] == "abstract data scene"


def test_missing_platform_copy_raises():
    bad = {"posts": [{"key": "p1", "image_prompt": "x", "platforms": {"linkedin": "only LI"}}]}
    with mock.patch.object(G, "llm_json", return_value=bad):
        with pytest.raises(G.GenerationError):
            G.generate_posts(CFG, "2026-07-27", 1)


def test_missing_image_prompt_raises():
    bad = {"posts": [{"key": "p1", "platforms": {"linkedin": "a", "x": "b"}}]}
    with mock.patch.object(G, "llm_json", return_value=bad):
        with pytest.raises(G.GenerationError):
            G.generate_posts(CFG, "2026-07-27", 1)


def test_empty_batch_raises():
    with mock.patch.object(G, "llm_json", return_value={"posts": []}):
        with pytest.raises(G.GenerationError):
            G.generate_posts(CFG, "2026-07-27", 1)
