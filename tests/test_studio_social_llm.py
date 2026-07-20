"""LLM seam: JSON parse + retries. NO assistant prefill (claude-sonnet-5 rejects
it with HTTP 400 -- the conversation must end with a user message)."""
from unittest import mock

import pytest

from services import studio_social_llm as L


def test_llm_json_parses_full_object():
    resp = {"content": [{"type": "text", "text": '{"posts": [{"key": "p1"}]}'}],
            "stop_reason": "end_turn"}
    with mock.patch.object(L, "_post_messages", return_value=resp):
        out = L.llm_json("sys", "user")
    assert out == {"posts": [{"key": "p1"}]}


def test_llm_json_tolerates_trailing_prose():
    resp = {"content": [{"type": "text", "text": '{"ok": true}\nHope that helps!'}]}
    with mock.patch.object(L, "_post_messages", return_value=resp):
        assert L.llm_json("sys", "user") == {"ok": True}


def test_llm_json_ends_with_user_message_no_prefill():
    captured = {}

    def fake_post(body, timeout=180):
        captured["body"] = body
        return {"content": [{"type": "text", "text": '{"ok": true}'}]}

    with mock.patch.object(L, "_post_messages", side_effect=fake_post):
        L.llm_json("sys", "user")
    msgs = captured["body"]["messages"]
    assert msgs[-1]["role"] == "user"          # sonnet-5 requires a user-terminated convo
    assert all(m["role"] != "assistant" for m in msgs)


def test_llm_json_retries_then_raises_on_persistent_bad_json():
    bad = {"content": [{"type": "text", "text": 'not json at all'}]}
    with mock.patch.object(L, "_post_messages", return_value=bad):
        with pytest.raises(L.LLMError):
            L.llm_json("sys", "user", retries=1)


def test_image_blocks_are_base64_png():
    blocks = L._image_blocks([b"PNG"])
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"]["media_type"] == "image/png"
