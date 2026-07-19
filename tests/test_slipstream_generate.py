import json
from unittest import mock

from services import slipstream_generate as sg


def _fake_anthropic_response(payload: dict):
    """Mimic the Anthropic Messages API response object shape (.content[0].text)."""
    block = mock.Mock()
    block.text = json.dumps(payload)
    resp = mock.Mock()
    resp.content = [block]
    return resp


VALID_PAYLOAD = {
    "title": "What Should a Dealer Map Before Buying AI",
    "description": "A diagnostic-first orientation for dealers evaluating AI tools.",
    "slug": "what-to-map-before-buying-ai",
    "body_mdx": "<AnswerFirst>Map the handoffs.</AnswerFirst>\n\n## Body\n\ntext",
    "image_prompts": [
        {"name": "hero", "prompt": "a cinematic diagram of a dealership handoff"},
        {"name": "gap", "prompt": "a gap between two systems"},
        {"name": "flow", "prompt": "a customer path flowing"},
    ],
    "social": {"linkedin": "LinkedIn draft", "x": "X draft"},
}


def _brand_cfg():
    return {"brand_key": "autointelligence", "business_key": "autointelligence",
            "voice": "restrained, diagnostic", "money_pages": ["/diagnostic-call"]}


def test_generate_post_parses_structured_output():
    with mock.patch.object(sg, "_anthropic_json", return_value=VALID_PAYLOAD):
        post = sg.generate_post(_brand_cfg(), topic="What to map before buying AI")
    assert post["slug"] == "what-to-map-before-buying-ai"
    assert post["title"].startswith("What Should a Dealer")
    assert len(post["image_prompts"]) == 3
    assert post["image_prompts"][0]["name"] == "hero"
    assert post["social"]["x"] == "X draft"


def test_generate_post_requires_hero_image_prompt():
    bad = dict(VALID_PAYLOAD, image_prompts=[{"name": "gap", "prompt": "x"}])
    with mock.patch.object(sg, "_anthropic_json", return_value=bad):
        try:
            sg.generate_post(_brand_cfg(), topic="t")
            assert False, "expected a hero-missing error"
        except sg.GenerationError as e:
            assert "hero" in str(e).lower()


def test_generate_post_missing_field_raises():
    bad = {k: v for k, v in VALID_PAYLOAD.items() if k != "body_mdx"}
    with mock.patch.object(sg, "_anthropic_json", return_value=bad):
        try:
            sg.generate_post(_brand_cfg(), topic="t")
            assert False, "expected a missing-field error"
        except sg.GenerationError as e:
            assert "body_mdx" in str(e).lower()


def test_anthropic_json_extracts_json_from_response():
    # _anthropic_json should call the client and json-parse the text block.
    fake_client = mock.Mock()
    fake_client.messages.create.return_value = _fake_anthropic_response({"ok": 1})
    with mock.patch.object(sg, "_client", return_value=fake_client):
        out = sg._anthropic_json("system", "user")
    assert out == {"ok": 1}
