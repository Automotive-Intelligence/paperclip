"""TDD for the WD generation variant: format==ts_posts_array returns a STRUCTURED
block array (body: Block[]) instead of an MDX string. The LLM call is mocked.
"""
from unittest import mock

from services import slipstream_generate as sg

WD_PAYLOAD = {
    "title": "How Do I Choose a Marketing Agency?",
    "description": "A plain owner-to-owner guide.",
    "slug": "how-do-i-choose-a-marketing-agency",
    "category": "Working With an Agency",
    "heroAlt": "a small-business owner at a desk",
    "ogTitle": "How Do I Choose a Marketing Agency?",
    "body": [
        {"type": "answer", "text": "Pick one that ties its work to real results."},
        {"type": "definition", "term": "Marketing agency", "text": "a partner that..."},
        {"type": "h2", "text": "What should I look for?"},
        {"type": "callout", "title": "The one idea", "text": "results over activity"},
        {"type": "quote", "text": "The tell is simple."},
        {"type": "image", "src": "/blog/how-do-i-choose-a-marketing-agency-a.png", "alt": "a"},
        {"type": "image", "src": "/blog/how-do-i-choose-a-marketing-agency-b.png", "alt": "b"},
        {"type": "links", "title": "See the work", "items": [{"label": "Get a sample", "href": "/quote"}]},
    ],
    "image_prompts": [{"name": "hero", "prompt": "h"}, {"name": "a", "prompt": "a"},
                      {"name": "b", "prompt": "b"}],
    "social": {"linkedin": "LinkedIn draft", "x": "X draft"},
}


def _wd_cfg():
    return {"brand_key": "worshipdigital", "business_key": "worshipdigital",
            "format": "ts_posts_array", "voice": "transparent, SMB-advocate",
            "money_pages": ["/quote", "/services"]}


def test_wd_generate_returns_block_array_not_mdx():
    with mock.patch.object(sg, "_llm_json", return_value=WD_PAYLOAD):
        post = sg.generate_post(_wd_cfg(), topic="choosing an agency")
    assert isinstance(post["body"], list)
    assert post["body"][0]["type"] == "answer"
    assert "body_mdx" not in post
    assert post["slug"] == "how-do-i-choose-a-marketing-agency"


def test_wd_generate_requires_answer_first_block():
    bad = dict(WD_PAYLOAD, body=[{"type": "p", "text": "x"}] + WD_PAYLOAD["body"])
    with mock.patch.object(sg, "_llm_json", return_value=bad):
        try:
            sg.generate_post(_wd_cfg(), topic="t")
            assert False, "expected an answer-first error"
        except sg.GenerationError as e:
            assert "answer" in str(e).lower()


def test_wd_generate_missing_body_raises():
    bad = {k: v for k, v in WD_PAYLOAD.items() if k != "body"}
    with mock.patch.object(sg, "_llm_json", return_value=bad):
        try:
            sg.generate_post(_wd_cfg(), topic="t")
            assert False, "expected a missing-field error"
        except sg.GenerationError as e:
            assert "body" in str(e).lower()


def test_wd_generate_requires_hero_image_prompt():
    bad = dict(WD_PAYLOAD, image_prompts=[{"name": "a", "prompt": "x"}])
    with mock.patch.object(sg, "_llm_json", return_value=bad):
        try:
            sg.generate_post(_wd_cfg(), topic="t")
            assert False, "expected a hero-missing error"
        except sg.GenerationError as e:
            assert "hero" in str(e).lower()
