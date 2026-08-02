"""Tests for services/cmo_daily_email — reality-first state + replyable footer."""

import services.cmo_daily_email as cde


_FAKE_BRANDS = [
    {"key": "autointelligence", "name": "Automotive Intelligence", "autonomy": "auto",
     "light": "🟢", "posts": ["AvI post"], "post_count": 1, "social": 2, "held": [],
     "signal_ok": True, "shipped_lines": ["blog: AvI post", "2 social posts (Zernio)"],
     "held_lines": []},
    {"key": "worshipdigital", "name": "Worship Digital", "autonomy": "auto",
     "light": "🟢", "posts": [], "post_count": 0, "social": 7,
     "held": [{"number": 20, "title": "WD held", "url": "http://x/20"}],
     "signal_ok": True, "shipped_lines": ["7 social posts (Zernio)"],
     "held_lines": ["PR #20 awaiting merge: WD held"]},
]


def test_fresh_overlay_ignores_stale(monkeypatch):
    stale = '{"date":"2026-06-24","headline":"OLD","needs_michael":["chore"]}'
    monkeypatch.setattr(cde, "_fetch_telemetry_path", lambda p: stale)
    assert cde._fresh_overlay("2026-08-02") == {}


def test_fresh_overlay_accepts_recent_decisions(monkeypatch):
    fresh = ('{"date":"2026-08-02","headline":"NEW",'
             '"decisions":[{"decision":"Sign the WD guarantee","default":"CMO holds it"}]}')
    monkeypatch.setattr(cde, "_fetch_telemetry_path", lambda p: fresh)
    ov = cde._fresh_overlay("2026-08-02")
    assert ov["headline"] == "NEW"
    assert ov["decisions"][0]["decision"] == "Sign the WD guarantee"


def test_load_state_uses_real_brands(monkeypatch):
    monkeypatch.setattr(cde, "_real_brands", lambda today: list(_FAKE_BRANDS))
    monkeypatch.setattr(cde, "_fetch_telemetry_path", lambda p: "")
    state = cde._load_state("2026-08-02")
    assert state["brands"] == _FAKE_BRANDS
    assert "shipped" in state["headline"].lower()  # auto headline reflects reality
    assert state["decisions"] == []


def test_auto_headline_counts():
    assert "1 blog post" in cde._auto_headline(_FAKE_BRANDS)
    assert "9 social" in cde._auto_headline(_FAKE_BRANDS)  # 2 + 7


def test_html_has_reply_to_and_no_false_nothing_shipped(monkeypatch):
    monkeypatch.delenv("CMO_DAILY_REPLY_TO", raising=False)
    state = {"headline": "h", "cmo_note": "n", "brands": _FAKE_BRANDS, "decisions": []}
    html = cde._build_html(state, "2026-08-02")
    assert "michael@automotiveintelligence.io" in html      # reply-to named in footer
    assert "Held / awaiting you" in html                    # new column
    assert "blog: AvI post" in html
    assert "Nothing needs you today" in html                # empty decisions default
    assert "nothing shipped" not in html.lower()


def test_text_lists_decisions_with_defaults():
    state = {"headline": "h", "cmo_note": "n", "brands": _FAKE_BRANDS,
             "decisions": [{"decision": "Pick the hero image", "default": "CMO picks A",
                            "action": "reply A or B"}]}
    text = cde._build_text(state, "2026-08-02")
    assert "Pick the hero image" in text
    assert "if silent: CMO picks A" in text
    assert "TO OVERRIDE: reply" in text


def test_send_includes_reply_to(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "k")
    monkeypatch.delenv("CMO_DAILY_REPLY_TO", raising=False)
    monkeypatch.setattr(cde, "_load_state", lambda today: {
        "headline": "h", "cmo_note": "n", "brands": _FAKE_BRANDS, "decisions": []})

    captured = {}
    class _Resp:
        ok = True
        headers = {"content-type": "application/json"}
        def json(self):
            return {"id": "eml_1"}
    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return _Resp()
    monkeypatch.setattr(cde.requests, "post", _fake_post)

    result = cde.send_cmo_daily()
    assert result["status"] == "sent"
    assert captured["payload"]["reply_to"] == "michael@automotiveintelligence.io"
    assert result["reply_to"] == "michael@automotiveintelligence.io"
