"""Unit tests for tools/audio_understanding.py.

Mocks the litellm boundary (_call_llm) and the byte-fetch boundary
(_load_audio_bytes) — no real OpenRouter calls, no cost, no network."""
from unittest import mock

import pytest

from tools import audio_understanding as A


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeUsage:
    def __init__(self, cost=None):
        self.cost = cost


class _FakeResponse:
    def __init__(self, content, usage_cost=None):
        self.choices = [_FakeChoice(content)]
        self.usage = _FakeUsage(usage_cost)


def test_guess_format_verified_extensions():
    assert A._guess_format("clip.wav") == "wav"
    assert A._guess_format("https://x.com/a/clip.mp3?sig=abc") == "mp3"


def test_guess_format_unknown_extension_returns_none():
    assert A._guess_format("clip.m4a") is None
    assert A._guess_format("clip") is None


def test_missing_api_key_returns_error_string(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    result = A.describe_audio("clip.wav")
    assert result.startswith("ERROR: OPENROUTER_API_KEY")


def test_unverified_format_returns_error_string(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    with mock.patch.object(A, "_load_audio_bytes", return_value=b"fake-bytes"):
        result = A.describe_audio("clip.ogg")
    assert "couldn't determine a verified audio format" in result


def test_oversized_audio_returns_error_string(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    big = b"x" * (A.MAX_AUDIO_BYTES + 1)
    with mock.patch.object(A, "_load_audio_bytes", return_value=big):
        result = A.describe_audio("clip.wav")
    assert "safety cap" in result


def test_fetch_failure_returns_error_string(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    with mock.patch.object(A, "_load_audio_bytes", side_effect=FileNotFoundError("nope")):
        result = A.describe_audio("missing.wav")
    assert result.startswith("ERROR fetching audio")


def test_happy_path_prefers_provider_reported_cost_at_full_precision(monkeypatch):
    """OpenRouter's real per-call cost (resp.usage.cost) takes priority over
    litellm's pricing-table estimate, and small costs must not round to $0.0000
    (that reads as 'free' when it wasn't — see module docstring / memory)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    captured = {}

    def fake_call_llm(messages, *, model, api_key):
        captured["messages"] = messages
        captured["model"] = model
        captured["api_key"] = api_key
        return _FakeResponse("Sounds like a dial tone.", usage_cost=0.0000231)

    with mock.patch.object(A, "_load_audio_bytes", return_value=b"RIFF...."), \
         mock.patch.object(A, "_call_llm", side_effect=fake_call_llm), \
         mock.patch.object(A, "_ensure_spend_tracking"), \
         mock.patch("litellm.completion_cost", return_value=0.9999):
        result = A.describe_audio("clip.wav")

    assert "Sounds like a dial tone." in result
    assert "[cost: $0.000023]" in result  # provider cost wins, not the 0.9999 litellm estimate
    assert captured["model"] == A.DEFAULT_MODEL
    content = captured["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "input_audio"
    assert content[1]["input_audio"]["format"] == "wav"


def test_falls_back_to_litellm_estimate_when_provider_cost_missing(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    with mock.patch.object(A, "_load_audio_bytes", return_value=b"RIFF...."), \
         mock.patch.object(A, "_call_llm", return_value=_FakeResponse("hi", usage_cost=None)), \
         mock.patch.object(A, "_ensure_spend_tracking"), \
         mock.patch("litellm.completion_cost", return_value=0.0006):
        result = A.describe_audio("clip.wav")
    assert "[cost: $0.000600]" in result


def test_cost_unavailable_says_so_rather_than_claiming_free(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    with mock.patch.object(A, "_load_audio_bytes", return_value=b"RIFF...."), \
         mock.patch.object(A, "_call_llm", return_value=_FakeResponse("hi", usage_cost=None)), \
         mock.patch.object(A, "_ensure_spend_tracking"), \
         mock.patch("litellm.completion_cost", side_effect=Exception("no pricing entry")):
        result = A.describe_audio("clip.wav")
    assert "[cost: not reported for this call]" in result
    assert "$0.0000" not in result


def test_llm_call_exception_returns_error_string(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    with mock.patch.object(A, "_load_audio_bytes", return_value=b"RIFF...."), \
         mock.patch.object(A, "_call_llm", side_effect=RuntimeError("boom")), \
         mock.patch.object(A, "_ensure_spend_tracking"):
        result = A.describe_audio("clip.wav")
    assert result.startswith("ERROR calling")
    assert "boom" in result


def test_tool_wrapper_delegates_to_describe_audio(monkeypatch):
    with mock.patch.object(A, "describe_audio", return_value="ok") as m:
        out = A.describe_audio_tool.func("clip.wav", "custom prompt")
    m.assert_called_once_with("clip.wav", "custom prompt")
    assert out == "ok"
