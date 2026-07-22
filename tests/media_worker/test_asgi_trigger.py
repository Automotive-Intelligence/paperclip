"""Fake-ASGI-harness tests for tools/media_worker/asgi.py's authenticated
POST /run-video trigger. No fastapi, no real render: run_job is monkeypatched
to a stub for the "valid token" path, and asserted un-called for the "invalid
token" path. RENDER_ON_STARTUP is unset in this test process, so importing
the module does not kick off a background render."""
import asyncio
import json
import os
import sys
import threading

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


def test_get_unknown_path_returns_404():
    # A typo'd path must NOT read as healthy, else the watchdog signal is coarse.
    scope = {"type": "http", "method": "GET", "path": "/healthz-typo", "headers": []}
    send, messages = make_send()

    asyncio.run(asgi.app(scope, make_receive(), send))

    assert status_of(messages) == 404


# ---- POST /poll (auto-trigger) ----

def _poll_scope(token=None):
    headers = [(b"authorization", ("Bearer " + token).encode())] if token else []
    return {"type": "http", "method": "POST", "path": "/poll", "headers": headers}


def _wait_lock_free(timeout=5.0):
    """Poll the shared render lock until it is re-acquirable (a background thread
    released it) or timeout. Returns True if it became free (and leaves it free)."""
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if asgi._render_lock.acquire(blocking=False):
            asgi._render_lock.release()
            return True
        time.sleep(0.02)
    return False


def test_poll_without_token_returns_401_and_does_not_poll(monkeypatch):
    monkeypatch.setenv("VIDEO_ROUTINE_TOKEN", "secret")
    called = []
    monkeypatch.setattr(asgi, "poll_once", lambda env: called.append(env))
    send, messages = make_send()
    asyncio.run(asgi.app(_poll_scope(None), make_receive(), send))
    assert status_of(messages) == 401
    assert called == []


def test_poll_with_token_spawns_poll_and_returns_polling(monkeypatch):
    monkeypatch.setenv("VIDEO_ROUTINE_TOKEN", "secret")
    done = threading.Event()
    seen = {}

    def fake_poll_once(env):
        seen["env"] = env
        done.set()
        return {"queued": 0, "rendered": []}

    monkeypatch.setattr(asgi, "poll_once", fake_poll_once)
    send, messages = make_send()
    asyncio.run(asgi.app(_poll_scope("secret"), make_receive(), send))
    assert status_of(messages) == 200
    assert json_body_of(messages)["status"] == "polling"
    assert done.wait(timeout=5), "poll_once should run in the background thread"
    assert "env" in seen  # the background thread actually invoked poll_once
    assert _wait_lock_free(), "the poll thread must release the render lock when done"


def test_poll_when_render_in_flight_returns_busy(monkeypatch):
    monkeypatch.setenv("VIDEO_ROUTINE_TOKEN", "secret")
    called = []
    monkeypatch.setattr(asgi, "poll_once", lambda env: called.append(env))
    # simulate a render already holding the shared lock
    assert asgi._render_lock.acquire(blocking=False)
    try:
        send, messages = make_send()
        asyncio.run(asgi.app(_poll_scope("secret"), make_receive(), send))
        assert status_of(messages) == 200
        assert json_body_of(messages)["status"] == "busy"
        assert called == []  # nothing rendered while busy
    finally:
        asgi._render_lock.release()


def test_run_video_when_render_in_flight_returns_409_busy(monkeypatch):
    monkeypatch.setenv("VIDEO_ROUTINE_TOKEN", "secret")
    called = []
    monkeypatch.setattr(asgi, "run_job", lambda env: called.append(env) or {"master_url": "u", "sheet_url": "s"})
    assert asgi._render_lock.acquire(blocking=False)
    try:
        scope = {"type": "http", "method": "POST", "path": "/run-video",
                 "headers": [(b"authorization", b"Bearer secret")]}
        send, messages = make_send()
        asyncio.run(asgi.app(scope, make_receive(b'{"take":"x"}'), send))
        assert status_of(messages) == 409
        assert json_body_of(messages)["status"] == "busy"
        assert called == []  # did not render while busy
    finally:
        asgi._render_lock.release()


# ---- lock-release invariant: a render error must NEVER leak the lock ----

def test_run_video_error_releases_lock(monkeypatch):
    """If run_job raises, /run-video returns 500 AND frees the render lock -- a
    leaked lock would wedge every future render at 409/busy."""
    monkeypatch.setenv("VIDEO_ROUTINE_TOKEN", "secret")

    def boom(env):
        raise RuntimeError("render blew up")
    monkeypatch.setattr(asgi, "run_job", boom)
    scope = {"type": "http", "method": "POST", "path": "/run-video",
             "headers": [(b"authorization", b"Bearer secret")]}
    send, messages = make_send()
    asyncio.run(asgi.app(scope, make_receive(b'{"take":"x"}'), send))
    assert status_of(messages) == 500
    assert _wait_lock_free(), "a 500 render error must not leak the render lock"


def test_poll_releases_lock_when_poll_once_raises(monkeypatch):
    """If poll_once raises in the background thread, the lock is still released
    (the thread's finally), so the worker is not wedged."""
    monkeypatch.setenv("VIDEO_ROUTINE_TOKEN", "secret")
    entered = threading.Event()

    def boom(env):
        entered.set()
        raise RuntimeError("poll blew up")
    monkeypatch.setattr(asgi, "poll_once", boom)
    send, messages = make_send()
    asyncio.run(asgi.app(_poll_scope("secret"), make_receive(), send))
    assert status_of(messages) == 200  # /poll returns before the thread runs
    assert entered.wait(timeout=5)
    assert _wait_lock_free(), "a failed poll cycle must release the render lock"


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
