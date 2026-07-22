"""Fake-ASGI-harness tests for tools/media_worker/asgi.py's authenticated
POST /run-video trigger. No fastapi, no real render: run_job is monkeypatched
to a stub for the "valid token" path, and asserted un-called for the "invalid
token" path. RENDER_ON_STARTUP is unset in this test process, so importing
the module does not kick off a background render."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import tools.media_worker.asgi as asgi


def make_receive(body: bytes = b""):
    state = {"sent": False}

    async def receive():
        if state["sent"]:
            return {"type": "http.disconnect"}
        state["sent"] = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


def make_send():
    messages = []

    async def send(message):
        messages.append(message)

    return send, messages


def status_of(messages):
    return next(m["status"] for m in messages if m["type"] == "http.response.start")


def json_body_of(messages):
    body = next(m["body"] for m in messages if m["type"] == "http.response.body")
    return json.loads(body) if body else None


def test_get_health_returns_200():
    scope = {"type": "http", "method": "GET", "path": "/health", "headers": []}
    send, messages = make_send()

    asyncio.run(asgi.app(scope, make_receive(), send))

    assert status_of(messages) == 200


def test_run_video_wrong_token_returns_401_and_does_not_render(monkeypatch):
    monkeypatch.setenv("VIDEO_ROUTINE_TOKEN", "the-real-token")
    calls = []
    monkeypatch.setattr(asgi, "run_job", lambda env: calls.append(env))

    scope = {
        "type": "http", "method": "POST", "path": "/run-video",
        "headers": [(b"authorization", b"Bearer wrong-token")],
    }
    body = json.dumps({"take": "raw/x.mp4", "edit": {"brand": "aipg"}}).encode("utf-8")
    send, messages = make_send()

    asyncio.run(asgi.app(scope, make_receive(body), send))

    assert status_of(messages) == 401
    assert calls == []  # run_job must never be called on a bad token


def test_run_video_missing_token_returns_401(monkeypatch):
    monkeypatch.setenv("VIDEO_ROUTINE_TOKEN", "the-real-token")
    calls = []
    monkeypatch.setattr(asgi, "run_job", lambda env: calls.append(env))

    scope = {"type": "http", "method": "POST", "path": "/run-video", "headers": []}
    body = json.dumps({"take": "raw/x.mp4"}).encode("utf-8")
    send, messages = make_send()

    asyncio.run(asgi.app(scope, make_receive(body), send))

    assert status_of(messages) == 401
    assert calls == []


def test_run_video_right_token_stages_and_passes_take_and_edit(monkeypatch):
    monkeypatch.setenv("VIDEO_ROUTINE_TOKEN", "the-real-token")
    received = {}

    def fake_run_job(env):
        received["env"] = env
        return {"master_url": "https://blob.example/renders_th/riverside.mp4",
                "sheet_url": "https://blob.example/renders_th/riverside.review.png"}

    monkeypatch.setattr(asgi, "run_job", fake_run_job)

    take = "raw_shoot_2026-07/aipg/riverside.mp4"
    edit = {"brand": "aipg", "hook": "opener"}
    scope = {
        "type": "http", "method": "POST", "path": "/run-video",
        "headers": [(b"authorization", b"Bearer the-real-token")],
    }
    body = json.dumps({"take": take, "edit": edit}).encode("utf-8")
    send, messages = make_send()

    asyncio.run(asgi.app(scope, make_receive(body), send))

    assert status_of(messages) == 200
    payload = json_body_of(messages)
    assert payload["status"] == "staged"
    assert payload["master_url"] == "https://blob.example/renders_th/riverside.mp4"
    assert payload["sheet_url"] == "https://blob.example/renders_th/riverside.review.png"

    assert received["env"]["TAKE_PATHNAME"] == take
    assert json.loads(received["env"]["EDIT_JSON"]) == edit


def test_run_video_without_edit_omits_edit_json_override(monkeypatch):
    """edit is optional; when omitted, EDIT_JSON is left for run_job's own
    {"brand": BRAND} default rather than forced to a literal null."""
    monkeypatch.setenv("VIDEO_ROUTINE_TOKEN", "the-real-token")
    monkeypatch.delenv("EDIT_JSON", raising=False)
    received = {}

    def fake_run_job(env):
        received["env"] = env
        return {"master_url": "https://blob.example/m.mp4", "sheet_url": "https://blob.example/s.png"}

    monkeypatch.setattr(asgi, "run_job", fake_run_job)

    scope = {
        "type": "http", "method": "POST", "path": "/run-video",
        "headers": [(b"authorization", b"Bearer the-real-token")],
    }
    body = json.dumps({"take": "raw/x.mp4"}).encode("utf-8")
    send, messages = make_send()

    asyncio.run(asgi.app(scope, make_receive(body), send))

    assert status_of(messages) == 200
    assert received["env"]["TAKE_PATHNAME"] == "raw/x.mp4"
    assert "EDIT_JSON" not in received["env"]


def test_run_video_render_error_returns_500(monkeypatch):
    monkeypatch.setenv("VIDEO_ROUTINE_TOKEN", "the-real-token")

    def fake_run_job(env):
        raise RuntimeError("render blew up")

    monkeypatch.setattr(asgi, "run_job", fake_run_job)

    scope = {
        "type": "http", "method": "POST", "path": "/run-video",
        "headers": [(b"authorization", b"Bearer the-real-token")],
    }
    body = json.dumps({"take": "raw/x.mp4"}).encode("utf-8")
    send, messages = make_send()

    asyncio.run(asgi.app(scope, make_receive(body), send))

    assert status_of(messages) == 500
    payload = json_body_of(messages)
    assert "render blew up" in payload["error"]
