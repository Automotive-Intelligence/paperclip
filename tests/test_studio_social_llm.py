"""LLM seam: assistant-prefill JSON forcing + defensive parse (no wire in tests)."""
from unittest import mock

import pytest

from services import studio_social_llm as L


def test_llm_json_reattaches_prefilled_brace():
    # With the "{" prefill, the API returns only the CONTINUATION after the brace.
    cont = {"content": [{"type": "text", "text": '"posts": [{"key": "p1"}]}'}],
            "stop_reason": "end_turn"}
    with mock.patch.object(L, "_post_messages", return_value=cont):
        out = L.llm_json("sys", "user")
    assert out == {"posts": [{"key": "p1"}]}


def test_llm_json_sends_assistant_prefill():
    captured = {}

    def fake_post(body, timeout=180):
        captured["body"] = body
        return {"content": [{"type": "text", "text": '"ok": true}'}]}

    with mock.patch.object(L, "_post_messages", side_effect=fake_post):
        L.llm_json("sys", "user")
    assert captured["body"]["messages"][-1] == {"role": "assistant", "content": "{"}


def test_llm_json_retries_then_raises_on_persistent_bad_json():
    bad = {"content": [{"type": "text", "text": 'not json at all'}]}
    with mock.patch.object(L, "_post_messages", return_value=bad):
        with pytest.raises(L.LLMError):
            L.llm_json("sys", "user", retries=1)


def test_image_blocks_are_base64_png():
    blocks = L._image_blocks([b"PNG"])
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"]["media_type"] == "image/png"
