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
    with mock.patch.object(sg, "_llm_json", return_value=VALID_PAYLOAD):
        post = sg.generate_post(_brand_cfg(), topic="What to map before buying AI")
    assert post["slug"] == "what-to-map-before-buying-ai"
    assert post["title"].startswith("What Should a Dealer")
    assert len(post["image_prompts"]) == 3
    assert post["image_prompts"][0]["name"] == "hero"
    assert post["social"]["x"] == "X draft"


def test_generate_post_requires_hero_image_prompt():
    bad = dict(VALID_PAYLOAD, image_prompts=[{"name": "gap", "prompt": "x"}])
    with mock.patch.object(sg, "_llm_json", return_value=bad):
        try:
            sg.generate_post(_brand_cfg(), topic="t")
            assert False, "expected a hero-missing error"
        except sg.GenerationError as e:
            assert "hero" in str(e).lower()


def test_generate_post_missing_field_raises():
    bad = {k: v for k, v in VALID_PAYLOAD.items() if k != "body_mdx"}
    with mock.patch.object(sg, "_llm_json", return_value=bad):
        try:
            sg.generate_post(_brand_cfg(), topic="t")
            assert False, "expected a missing-field error"
        except sg.GenerationError as e:
            assert "body_mdx" in str(e).lower()


def test_llm_json_extracts_json_from_openrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")

    class _Resp:
        ok = True
        def json(self):
            return {"choices": [{"message": {"content": '```json\n{"ok": 1}\n```'}}]}

    with mock.patch.object(sg.requests, "post", return_value=_Resp()):
        out = sg._llm_json("system", "user")
    assert out == {"ok": 1}


def test_llm_json_ignores_trailing_prose(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")

    class _Resp:
        ok = True
        def json(self):
            # a valid JSON object followed by trailing prose (the real 07-19 failure)
            return {"choices": [{"message": {"content": '{"ok": 1, "a": "b"}\n\nHope this helps!'}}]}

    with mock.patch.object(sg.requests, "post", return_value=_Resp()):
        out = sg._llm_json("s", "u")
    assert out == {"ok": 1, "a": "b"}


def test_llm_json_none_content_raises(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")

    class _Resp:
        ok = True
        def json(self):
            return {"choices": [{"message": {"content": None}, "finish_reason": "content_filter"}]}

    with mock.patch.object(sg.requests, "post", return_value=_Resp()):
        try:
            sg._llm_json("s", "u")
            assert False, "expected GenerationError on None content"
        except sg.GenerationError as e:
            assert "empty" in str(e).lower()
