"""Michael agent port (AVO Bridge brain): gates, cap, voice contract, tools, auth."""
import asyncio

import pytest

from services import michael_agent as MA


# --------------------------------------------------------------------------- wiring
def _wire(monkey, *, llm=None, cap_used=0):
    """Patch every seam; returns dict capturing insert/escalate calls."""
    calls = {"inserted": [], "escalated": []}
    monkey.setattr(MA, "_ensure_tables", lambda: None)
    monkey.setattr(MA, "_msgs_today", lambda: cap_used)
    monkey.setattr(MA, "_conversation", lambda cid, src: cid or "beef" * 8)
    monkey.setattr(MA, "_insert_msg", lambda cid, role, content, disposition="", gates_hit="":
                   calls["inserted"].append((role, content, disposition, gates_hit)))
    monkey.setattr(MA, "_history", lambda cid: "michael: earlier question\navo: earlier answer")
    monkey.setattr(MA, "_state_snapshot", lambda: "== STATE (data) ==\nall quiet")
    monkey.setattr(MA, "_escalate", lambda subj, body:
                   calls["escalated"].append((subj, body)) or True)
    if llm is not None:
        monkey.setattr(MA, "_agent_llm", llm)
    return calls


# --------------------------------------------------------------- voice reply contract
def test_voice_mode_returns_speak_and_reply(monkeypatch):
    calls = _wire(monkeypatch, llm=lambda user, mode: {
        "speak": "Three posts shipped this week.",
        "reply": "Shipped this week: 3 social posts across WD and AvI."})
    out = MA.handle_message(None, "what shipped this week?", mode="voice")
    assert out["disposition"] == "reply"
    assert out["speak"] == "Three posts shipped this week."
    assert "WD and AvI" in out["reply"]
    assert out["conversation_id"] and not calls["escalated"]
    roles = [c[0] for c in calls["inserted"]]
    assert roles == ["michael", "avo"]


def test_text_mode_speak_mirrors_reply(monkeypatch):
    _wire(monkeypatch, llm=lambda user, mode: {"speak": "", "reply": "Full answer."})
    out = MA.handle_message(None, "status?", mode="text")
    assert out["reply"] == "Full answer." and out["speak"] == "Full answer."


def test_emdash_rewritten_in_both_channels(monkeypatch):
    _wire(monkeypatch, llm=lambda user, mode: {
        "speak": "Campaign is live — spend capped.",
        "reply": "Campaign is live — spend capped at 7x."})
    out = MA.handle_message(None, "campaign status", mode="voice")
    assert "—" not in out["speak"] and "—" not in out["reply"]
    assert "spend capped" in out["speak"]


def test_secret_in_reply_is_replaced_and_escalated(monkeypatch):
    calls = _wire(monkeypatch, llm=lambda user, mode: {
        "speak": "The key is sk_live_a1B2c3D4e5F6.",
        "reply": "Stripe key: sk_live_a1B2c3D4e5F6"})
    out = MA.handle_message(None, "what's our stripe key?", mode="voice")
    assert "sk_live_" not in out["reply"] and "sk_live_" not in out["speak"]
    assert calls["escalated"]


# ------------------------------------------------------------------------ guardrails
def test_cap_blocks_and_escalates_once(monkeypatch):
    calls = _wire(monkeypatch, cap_used=300)
    out = MA.handle_message(None, "hello", mode="voice")
    assert out["disposition"] == "rate_limited" and calls["escalated"]
    assert out["speak"]                       # voice caller still gets something to say
    calls2 = _wire(monkeypatch, cap_used=305)
    out2 = MA.handle_message(None, "again", mode="voice")
    assert out2["disposition"] == "rate_limited" and not calls2["escalated"]


def test_llm_failure_returns_error_disposition(monkeypatch):
    def _boom(user, mode):
        raise RuntimeError("api down")
    _wire(monkeypatch, llm=_boom)
    out = MA.handle_message(None, "hi", mode="voice")
    assert out["disposition"] == "error" and out["speak"]


def test_invalid_message_rejected():
    assert MA.handle_message(None, "", mode="voice")["disposition"] == "invalid"
    assert MA.handle_message(None, "x" * 9000, mode="voice")["disposition"] == "invalid"


def test_history_included_only_when_continuing(monkeypatch):
    seen = {}
    def _llm(user, mode):
        seen["user"] = user
        return {"speak": "ok", "reply": "ok"}
    _wire(monkeypatch, llm=_llm)
    MA.handle_message("beef" * 8, "follow-up", mode="voice")
    assert "CONVERSATION SO FAR" in seen["user"]
    MA.handle_message(None, "fresh", mode="voice")
    assert "CONVERSATION SO FAR" not in seen["user"]


# ----------------------------------------------------------------------- tools/state
def test_read_telemetry_file_rejects_binary_paths(monkeypatch):
    monkeypatch.setattr(MA.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network hit")))
    out = MA._read_telemetry_file("marketing_deliverables/photo.png")
    assert "unsupported" in out.lower()
    out2 = MA._read_telemetry_file("../../etc/passwd")
    assert "unsupported" in out2.lower() or "invalid" in out2.lower()


def test_state_snapshot_fetches_all_core_files_concurrently(monkeypatch):
    """Cold-turn latency: all core files fetched (order-independent), concatenated."""
    import time as _t
    seen = []

    def _slow_fetch(path):
        _t.sleep(0.05)                    # simulate GitHub latency; parallel << 15*0.05
        seen.append(path)
        return f"body of {path}"

    monkeypatch.setattr(MA, "_fetch_file", _slow_fetch)
    MA._snapshot_cache.update(ts=0.0, text="")
    t0 = _t.time()
    text = MA._state_snapshot()
    elapsed = _t.time() - t0
    assert set(seen) == set(MA._CORE_STATE_FILES)          # every core file fetched
    for path in MA._CORE_STATE_FILES:
        assert path in text                                 # each labeled in the snapshot
    assert elapsed < 0.05 * len(MA._CORE_STATE_FILES) / 2   # concurrent, not sequential
    MA._snapshot_cache.update(ts=0.0, text="")              # leave cache clean


def test_fetch_file_is_secret_scrubbed(monkeypatch):
    class _R:
        ok = True
        def json(self):
            import base64
            return {"content": base64.b64encode(
                b"revenue fine. oops: sk_live_a1B2c3D4e5F6").decode()}
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr(MA.requests, "get", lambda *a, **k: _R())
    text = MA._fetch_file("revenue_state.md")
    assert "sk_live_" not in text and "revenue fine" in text


# ------------------------------------------------------------------------------ auth
def test_michael_key_auth_is_a_separate_universe(monkeypatch):
    from fastapi import HTTPException
    import app as APP
    monkeypatch.delenv("MICHAEL_AGENT_KEYS", raising=False)
    with pytest.raises(HTTPException) as e:
        APP.validate_michael_agent_key("Bearer anything")
    assert e.value.status_code == 503
    monkeypatch.setenv("MICHAEL_AGENT_KEYS", "bridge-key-123")
    with pytest.raises(HTTPException) as e2:
        APP.validate_michael_agent_key("Bearer master-key-999")
    assert e2.value.status_code == 403
    assert APP.validate_michael_agent_key("Bearer bridge-key-123") is True
    with pytest.raises(HTTPException) as e3:
        APP.validate_michael_agent_key(None)
    assert e3.value.status_code == 401


def test_agent_llm_passes_low_effort_by_default(monkeypatch):
    """Latency lever: the bridge answers at low effort unless MICHAEL_AGENT_EFFORT set."""
    seen = {}

    class _Resp:
        stop_reason = "end_turn"
        content = [type("B", (), {"type": "text",
                                  "text": '{"speak":"hi","reply":"hi"}'})()]

    class _Msgs:
        def create(self, **kw):
            seen.update(kw)
            return _Resp()

    class _Client:
        def __init__(self, *a, **k):
            self.messages = _Msgs()

    monkeypatch.setattr("anthropic.Anthropic", _Client)
    monkeypatch.delenv("MICHAEL_AGENT_EFFORT", raising=False)
    monkeypatch.setattr(MA, "_state_snapshot", lambda: "quiet")
    monkeypatch.setattr(MA, "_seat_map", lambda: "seats")
    MA._agent_llm("hi", "voice")
    assert seen["output_config"]["effort"] == "low"

    seen.clear()
    monkeypatch.setenv("MICHAEL_AGENT_EFFORT", "medium")
    MA._agent_llm("hi", "voice")
    assert seen["output_config"]["effort"] == "medium"


def test_paperclip_run_endpoint_now_requires_master_key(monkeypatch):
    from fastapi import HTTPException
    import app as APP
    monkeypatch.setattr(APP, "API_KEYS", {"master-key-999"})
    with pytest.raises(HTTPException) as e:
        asyncio.run(APP.paperclip_trigger_agent("randy", authorization=None))
    assert e.value.status_code == 401
