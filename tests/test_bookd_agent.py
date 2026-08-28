"""Book'd partner-agent port: wall gates, credential staging, cap, MCP dispatch, auth."""
import json
from unittest import mock

import pytest

from services import bookd_agent as BA
from services import bookd_mcp as BM


# --------------------------------------------------------------------------- gates
def test_scrub_secrets_catches_stripe_slack_github_shapes():
    text = ("key sk_live_a1B2c3D4e5F6 plus whsec_ZZ99xx88yy77 and xoxb-123456789-abc "
            "and ghp_abcdefghijklmnopqrstu and price_1TjjoT0OY3MGQ5Iy4gN7clMZ")
    redacted, found = BA.scrub_secrets(text)
    assert len(found) == 4                                  # price id is NOT a secret
    assert "sk_live_" not in redacted and "whsec_" not in redacted
    assert "price_1TjjoT0OY3MGQ5Iy4gN7clMZ" in redacted     # price ids pass through


def test_gate_reply_secret_and_brand_leak_replace_and_flag():
    reply, hits = BA._gate_reply("your key is sk_live_a1B2c3D4e5F6")
    assert "secret" in hits and "sk_live_" not in reply and "flagged" in reply.lower()
    reply2, hits2 = BA._gate_reply("Miriam's Paper & Purpose store does this too")
    assert "brand_leak" in hits2 and "Miriam" not in reply2


def test_gate_reply_rewrites_emdash_but_does_not_block():
    reply, hits = BA._gate_reply("Stripe prices are set — webhook secret is pending")
    assert hits == ["emdash_rewrite"]
    assert "—" not in reply and "webhook secret is pending" in reply


def test_guess_key_name_prefers_name_value_form_then_prefix():
    txt = "STRIPE_WEBHOOK_SECRET=whsec_abc12345678 and also rk_live_zz99887766aa"
    assert BA._guess_key_name("whsec_abc12345678", txt) == "STRIPE_WEBHOOK_SECRET"
    assert BA._guess_key_name("rk_live_zz99887766aa", "bare paste") == "STRIPE_SECRET_KEY"
    assert BA._guess_key_name("odd-shape-value-123", "??") == "UNKNOWN_SECRET"


# ------------------------------------------------------------------- handle_message
def _wire(monkey, *, llm=None, cap_used=0):
    """Patch every seam; returns dict capturing insert calls."""
    calls = {"inserted": [], "escalated": []}
    monkey.setattr(BA, "_ensure_tables", lambda: None)
    monkey.setattr(BA, "_partner_msgs_today", lambda: cap_used)
    monkey.setattr(BA, "_conversation", lambda cid, src: cid or "cafe" * 8)
    monkey.setattr(BA, "_insert_msg", lambda cid, role, content, disposition="", gates_hit="":
                   calls["inserted"].append((role, content, disposition, gates_hit)))
    monkey.setattr(BA, "_history", lambda cid: "partner: earlier question\navo: earlier answer")
    monkey.setattr(BA, "_system_prompt", lambda scope="bookd": "WALL")
    monkey.setattr(BA, "_escalate", lambda subj, body:
                   calls["escalated"].append((subj, body)) or True)
    monkey.setattr(BA, "_state_log", lambda cid, src: None)
    if llm is not None:
        monkey.setattr(BA, "llm_json", llm)
    return calls


def test_reply_path(monkeypatch):
    calls = _wire(monkeypatch, llm=lambda s, u, retries=1:
                  {"disposition": "reply", "reply": "Stripe prices are live.", "reason": "ok"})
    out = BA.handle_message(None, "what is the stripe status?")
    assert out["disposition"] == "reply" and "Stripe prices are live." in out["reply"]
    assert out["conversation_id"] and not calls["escalated"]
    roles = [c[0] for c in calls["inserted"]]
    assert roles == ["partner", "avo"]


def test_llm_escalate_path_sends_alert(monkeypatch):
    calls = _wire(monkeypatch, llm=lambda s, u, retries=1:
                  {"disposition": "escalate", "reply": "Flagged to Michael.", "reason": "needs decision"})
    out = BA.handle_message(None, "please deploy the new pricing page")
    assert out["disposition"] == "escalate" and calls["escalated"]


def test_gate_forced_escalation_on_brand_leak(monkeypatch):
    calls = _wire(monkeypatch, llm=lambda s, u, retries=1:
                  {"disposition": "reply", "reply": "Like we did for Worship Digital...", "reason": ""})
    out = BA.handle_message(None, "how do we compare to other builds?")
    assert out["disposition"] == "escalate"
    assert "worship" not in out["reply"].lower()
    assert calls["escalated"]


def test_inbound_credential_staged_and_redacted(monkeypatch):
    calls = _wire(monkeypatch, llm=lambda s, u, retries=1:
                  {"disposition": "reply", "reply": "Got it.", "reason": ""})
    staged = []
    monkeypatch.setattr("services.bookd_handoff.stage",
                        lambda name, val, submitted_by: staged.append((name, val))
                        or {"ok": True, "id": 7, "key_name": name, "status": "staged"})
    out = BA.handle_message(None, "here you go STRIPE_WEBHOOK_SECRET=whsec_abc12345678")
    assert staged and staged[0][0] == "STRIPE_WEBHOOK_SECRET"
    assert "staged encrypted as #7" in out["reply"]
    # plaintext never lands in the conversation store
    partner_rows = [c for c in calls["inserted"] if c[0] == "partner"]
    assert partner_rows and "whsec_abc12345678" not in partner_rows[0][1]


def test_rate_cap_blocks_and_escalates_once(monkeypatch):
    calls = _wire(monkeypatch, cap_used=200)
    monkeypatch.setenv("BOOKD_AGENT_DAILY_CAP", "200")
    out = BA.handle_message(None, "hello")
    assert out["disposition"] == "rate_limited" and calls["escalated"]
    calls2 = _wire(monkeypatch, cap_used=205)         # past the crossing: no re-alert
    out2 = BA.handle_message(None, "hello again")
    assert out2["disposition"] == "rate_limited" and not calls2["escalated"]


def test_llm_failure_returns_error_disposition(monkeypatch):
    def _boom(s, u, retries=1):
        raise RuntimeError("api down")
    _wire(monkeypatch, llm=_boom)
    out = BA.handle_message(None, "hi")
    assert out["disposition"] == "error" and "retry" in out["reply"].lower()


def test_invalid_message_rejected():
    assert BA.handle_message(None, "")["disposition"] == "invalid"
    assert BA.handle_message(None, "x" * 9000)["disposition"] == "invalid"


def test_history_included_only_when_continuing(monkeypatch):
    seen = {}
    def _llm(system, user, retries=1):
        seen["user"] = user
        return {"disposition": "reply", "reply": "ok", "reason": ""}
    _wire(monkeypatch, llm=_llm)
    BA.handle_message("cafe" * 8, "follow-up")
    assert "CONVERSATION SO FAR" in seen["user"]
    BA.handle_message(None, "fresh")
    assert "CONVERSATION SO FAR" not in seen["user"]


def test_state_fetch_is_secret_scrubbed(monkeypatch):
    class _R:
        ok = True
        def json(self):
            import base64
            return {"content": base64.b64encode(
                b"status fine. leaked: sk_live_a1B2c3D4e5F6").decode()}
    monkeypatch.setenv("SLIPSTREAM_GH_TOKEN", "t")
    monkeypatch.setattr(BA.requests, "get", lambda *a, **k: _R())
    state = BA._fetch_state()
    assert "sk_live_" not in state and "status fine" in state


# ---------------------------------------------------------------------------- MCP
def test_mcp_initialize_and_tools_list():
    init = BM.handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": "2025-03-26"}})
    assert init["result"]["protocolVersion"] == "2025-03-26"
    assert init["result"]["serverInfo"]["name"] == "avo-bookd-port"
    tools = BM.handle_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in tools["result"]["tools"]}
    # The mailbox and the activity feed exist at EVERY scope: staying in sync with what
    # AVO is doing is not a privilege, it is the point of having a partner port. The
    # avo-scope-only tools (search/read/act) are asserted separately below.
    assert names == {"bookd_message", "bookd_status", "bookd_handoff_secret",
                     "avo_activity", "avo_inbox", "avo_send_note"}
    assert not names & {"avo_search", "avo_read", "request_action", "check_action"}


def test_mcp_notification_returns_none_and_unknown_method_errors():
    assert BM.handle_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    err = BM.handle_rpc({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
    assert err["error"]["code"] == -32601


def test_mcp_tool_call_dispatches_to_core(monkeypatch):
    monkeypatch.setattr("services.bookd_agent.handle_message",
                        lambda cid, msg, **kw: {"conversation_id": "c1",
                                                "disposition": "reply", "reply": "hi"})
    out = BM.handle_rpc({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                         "params": {"name": "bookd_message",
                                    "arguments": {"message": "hello"}}},
                        {"id": 1, "label": "t", "scope": "bookd", "can_act": False})
    payload = json.loads(out["result"]["content"][0]["text"])
    assert payload["reply"] == "hi" and out["result"]["isError"] is False


# ---------------------------------------------------------------------------- auth
def test_bookd_key_auth_is_a_separate_universe(monkeypatch):
    """Keys resolve through the store (so revocation is instant), and the partner
    surface and the master surface stay separate credential universes."""
    from fastapi import HTTPException
    import app as APP

    grants = {"ryan-key-123": {"id": 1, "label": "ryan-hermes",
                               "scope": "avo", "can_act": True}}
    monkeypatch.setattr("services.partner_keys.resolve", lambda raw: grants.get(raw))

    # a master-style key is NOT a partner key
    with pytest.raises(HTTPException) as e2:
        APP.validate_bookd_agent_key("Bearer master-key-999")
    assert e2.value.status_code == 403

    # the issued key resolves to its GRANT (scope comes from the key, never the request)
    grant = APP.validate_bookd_agent_key("Bearer ryan-key-123")
    assert grant["scope"] == "avo" and grant["can_act"] is True

    # revoked/unknown -> resolve() returns None -> denied on the very next request
    grants.clear()
    with pytest.raises(HTTPException) as e4:
        APP.validate_bookd_agent_key("Bearer ryan-key-123")
    assert e4.value.status_code == 403

    # missing/bad header form
    with pytest.raises(HTTPException) as e3:
        APP.validate_bookd_agent_key(None)
    assert e3.value.status_code == 401
