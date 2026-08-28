"""Partner port: key scopes, instant revocation, scope enforcement, action tiers."""
from unittest import mock

import pytest

from services import avo_state as AS
from services import bookd_mcp as BM
from services import partner_actions as PA
from services import partner_keys as PK


# ------------------------------------------------------------------- key store
def test_key_is_stored_as_hash_never_raw(monkeypatch):
    writes = []
    monkeypatch.setattr(PK, "execute_query", lambda sql, params=None: None)
    monkeypatch.setattr(PK, "fetch_all",
                        lambda sql, params=None: writes.append((sql, params)) or [(1,)])
    out = PK.issue("ryan-hermes", "avo", can_act=True, raw_key="super-secret-raw")
    assert out["ok"] and out["key"] == "super-secret-raw"      # returned once to the caller
    blob = " ".join(str(w) for w in writes)
    assert "super-secret-raw" not in blob                       # only the hash is persisted
    assert PK._hash("super-secret-raw") in blob


def test_resolve_rejects_revoked_and_unknown(monkeypatch):
    monkeypatch.setattr(PK, "execute_query", lambda sql, params=None: None)
    monkeypatch.setattr(PK, "fetch_all",
                        lambda sql, params=None: [(1, "ryan", "avo", "revoked", True)])
    assert PK.resolve("anything") is None                       # revoked -> denied
    monkeypatch.setattr(PK, "fetch_all", lambda sql, params=None: [])
    assert PK.resolve("anything") is None                       # unknown -> denied


def test_resolve_fails_closed_when_store_unreadable(monkeypatch):
    monkeypatch.setattr(PK, "execute_query", lambda sql, params=None: None)
    monkeypatch.setattr(PK, "fetch_all",
                        mock.Mock(side_effect=RuntimeError("db down")))
    assert PK.resolve("valid-key") is None                      # DB down -> DENY, not allow


def test_resolve_returns_grant(monkeypatch):
    monkeypatch.setattr(PK, "execute_query", lambda sql, params=None: None)
    monkeypatch.setattr(PK, "fetch_all",
                        lambda sql, params=None: [(7, "ryan-hermes", "avo", "active", True)])
    g = PK.resolve("k")
    assert g == {"id": 7, "label": "ryan-hermes", "scope": "avo", "can_act": True}


def test_issue_rejects_unknown_scope():
    assert PK.issue("x", "everything")["ok"] is False


# ------------------------------------------------------------- scope enforcement
def test_bookd_scope_cannot_see_or_call_avo_tools():
    bookd = {"id": 1, "label": "b", "scope": "bookd", "can_act": False}
    names = {t["name"] for t in BM.tools_for("bookd")}
    assert "avo_search" not in names and "bookd_message" in names
    # naming the tool directly is still refused: the list is a hint, not the gate
    out = BM._call_tool("avo_search", {"query": "revenue"}, bookd)
    assert out["isError"] is True and "requires 'avo' scope" in out["content"][0]["text"]


def test_avo_scope_sees_the_full_toolset():
    names = {t["name"] for t in BM.tools_for("avo")}
    assert {"avo_search", "avo_read", "request_action", "check_action"} <= names


def test_action_channel_off_blocks_requests_even_at_avo_scope():
    grant = {"id": 1, "label": "r", "scope": "avo", "can_act": False}
    out = BM._call_tool("request_action", {"action": "deploy the site"}, grant)
    assert out["isError"] is True and "action channel is turned off" in out["content"][0]["text"]


def test_avo_state_read_refuses_a_file_outside_scope(monkeypatch):
    monkeypatch.setattr(AS, "_list_files",
                        lambda: [{"path": "bookd_state.md", "bytes": 10},
                                 {"path": "revenue_state.md", "bytes": 99}])
    assert AS.read("revenue_state.md", "bookd")["ok"] is False   # walled
    monkeypatch.setattr(AS, "_fetch", lambda p: "# Revenue\nsome numbers")
    assert AS.read("revenue_state.md", "avo")["ok"] is True       # granted


def test_avo_state_scrubs_secrets_from_everything(monkeypatch):
    monkeypatch.setenv("SLIPSTREAM_GH_TOKEN", "t")
    AS._cache.clear()
    class _R:
        ok = True
        def json(self):
            import base64
            return {"content": base64.b64encode(b"key sk_live_a1B2c3D4e5F6 here").decode()}
    monkeypatch.setattr(AS.requests, "get", lambda *a, **k: _R())
    assert "sk_live_" not in AS._fetch("any.md")
    AS._cache.clear()


# ----------------------------------------------------------------- action tiers
def test_classify_gates_blast_radius_and_fails_closed():
    for risky in ("send an email to the client list", "deploy the control center",
                  "increase the ad budget to 500", "rotate the stripe key",
                  "delete the old campaign", "publish the post"):
        assert PA.classify(risky) == "gated", risky
    for safe in ("read revenue_state.md", "summarize the funnel status",
                 "search for the canary results", "compare last two weeks"):
        assert PA.classify(safe) == "open", safe
    assert PA.classify("frobnicate the widget") == "gated"      # unknown -> fail closed
    assert PA.classify("") == "gated"


def test_gated_request_stages_and_pages_michael(monkeypatch):
    alerts = []
    monkeypatch.setattr(PA, "execute_query", lambda sql, params=None: None)
    monkeypatch.setattr(PA, "fetch_all", lambda sql, params=None: [(11,)])
    monkeypatch.setattr(PA, "_alert", lambda s, t: alerts.append((s, t)) or True)
    out = PA.request_action("send the proposal to the client", {"to": "x"},
                            key_id=1, requested_by="ryan-hermes")
    assert out["status"] == "pending" and out["tier"] == "gated"
    assert alerts and "approval needed" in alerts[0][0]


def test_open_request_is_recorded_without_paging(monkeypatch):
    alerts = []
    monkeypatch.setattr(PA, "execute_query", lambda sql, params=None: None)
    monkeypatch.setattr(PA, "fetch_all", lambda sql, params=None: [(12,)])
    monkeypatch.setattr(PA, "_alert", lambda s, t: alerts.append(s) or True)
    out = PA.request_action("summarize the AIPG funnel", {}, key_id=1)
    assert out["status"] == "recorded" and out["tier"] == "open" and not alerts


def test_approve_only_moves_pending_requests(monkeypatch):
    state = {"status": "pending"}
    monkeypatch.setattr(PA, "execute_query", lambda sql, params=None:
                        state.update(status="approved") if "UPDATE" in str(sql) else None)
    monkeypatch.setattr(PA, "fetch_all",
                        lambda sql, params=None: [(state["status"], "do the thing")])
    assert PA.approve(11)["status"] == "approved"
    assert PA.approve(11)["ok"] is False        # already approved, not re-approvable


def test_request_failure_reports_nothing_was_done(monkeypatch):
    monkeypatch.setattr(PA, "execute_query", mock.Mock(side_effect=RuntimeError("db")))
    out = PA.request_action("deploy something")
    assert out["ok"] is False and "nothing was done" in out["error"]


# --------------------------------------------------------------------- the wall
def test_secret_gate_holds_at_avo_scope_but_brand_gate_does_not():
    from services import bookd_agent as BA
    # secrets: blocked at EVERY scope
    reply, hits = BA._gate_reply("the key is sk_live_a1B2c3D4e5F6", "avo")
    assert "secret" in hits and "sk_live_" not in reply
    # cross-brand: that IS the bookd wall, so it must not gag a full-access partner
    reply2, hits2 = BA._gate_reply("Worship Digital revenue is up", "avo")
    assert "brand_leak" not in hits2 and "Worship Digital" in reply2
    reply3, hits3 = BA._gate_reply("Worship Digital revenue is up", "bookd")
    assert "brand_leak" in hits3


# --------------------------------------------------------------- kill switch
def test_revoking_a_key_that_does_not_exist_reports_failure():
    """Regression: revoke() returned ok:True unconditionally, so a typo'd id -- or a
    caller sending key_id instead of id, landing on id=0 -- looked exactly like a
    successful revocation while the key stayed ACTIVE. On the control that cuts off a
    partner, a success claim must mean the row actually changed."""
    import services.partner_keys as PK

    calls = []

    def fake_fetch_all(sql, params=()):
        calls.append(sql)
        s = " ".join(sql.split())
        if s.startswith("UPDATE partner_agent_keys SET status='revoked'"):
            return []          # matched no row
        if s.startswith("SELECT status FROM partner_agent_keys"):
            return []          # and no such key exists
        return []

    orig_fetch, orig_ensure = PK.fetch_all, PK.ensure_table
    PK.fetch_all, PK.ensure_table = fake_fetch_all, lambda: None
    try:
        out = PK.revoke(0, "typo")
        assert out["ok"] is False
        assert "NOTHING was revoked" in out["error"]
    finally:
        PK.fetch_all, PK.ensure_table = orig_fetch, orig_ensure


def test_revoking_a_real_key_confirms_the_row_changed():
    import services.partner_keys as PK

    def fake_fetch_all(sql, params=()):
        if " ".join(sql.split()).startswith("UPDATE partner_agent_keys SET status='revoked'"):
            return [(7, "ryan-hermes")]
        return []

    orig_fetch, orig_ensure = PK.fetch_all, PK.ensure_table
    PK.fetch_all, PK.ensure_table = fake_fetch_all, lambda: None
    try:
        out = PK.revoke(7, "went sideways")
        assert out["ok"] is True and out["id"] == 7 and out["label"] == "ryan-hermes"
    finally:
        PK.fetch_all, PK.ensure_table = orig_fetch, orig_ensure


def test_already_revoked_is_success_not_an_error():
    import services.partner_keys as PK

    def fake_fetch_all(sql, params=()):
        s = " ".join(sql.split())
        if s.startswith("UPDATE partner_agent_keys SET status='revoked'"):
            return []
        if s.startswith("SELECT status FROM partner_agent_keys"):
            return [("revoked",)]
        return []

    orig_fetch, orig_ensure = PK.fetch_all, PK.ensure_table
    PK.fetch_all, PK.ensure_table = fake_fetch_all, lambda: None
    try:
        out = PK.revoke(7, "again")
        assert out["ok"] is True and "already revoked" in out.get("note", "")
    finally:
        PK.fetch_all, PK.ensure_table = orig_fetch, orig_ensure
