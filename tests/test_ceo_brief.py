"""CEO Brief: exception-first order, one-line collapse, honest spend states, token."""
import datetime as dt
from unittest import mock

from services import ceo_brief as CB


def _data(**over):
    base = {
        "date": "2026-11-07", "generated_at": "2026-11-07T13:00:00+00:00",
        "needs_you": [], "money": {"pipeline": {"wired": False}, "mrr": {"wired": False},
                                   "ai_spend": {"state": "idle", "yesterday_usd": 0.0,
                                                "calls": 0, "mtd_usd": 0.0}},
        "scoreboard": {"majors": [{"key": "aipg", "name": "The AI Phone Guy",
                                   "lines": ["Leads: 0 yesterday, 2 last 7 days"]}],
                       "minors": []},
        "broken_signals": [], "shipped": {"brands": [], "ape": []},
        "page_url": "https://x/brief?k=abc",
    }
    base.update(over)
    return base


def test_subject_zero_day_says_nothing_needs_you():
    assert CB.subject(_data()) == "CEO Brief Sat Nov 7: Nothing needs you"


def test_subject_counts_needs_and_broken():
    d = _data(needs_you=[{"kind": "sonar", "title": "x"}],
              broken_signals=[{"key": "a", "text": "t"}, {"key": "b", "text": "u"}])
    assert CB.subject(d) == "CEO Brief Sat Nov 7: 1 needs you · 2 broken signals"


def test_render_order_and_collapse_lines():
    html = CB.render_html(_data(), "https://x/brief?k=abc")
    i1, i2, i3, i4, i5 = (html.index("1. Needs you"), html.index("2. Money"),
                          html.index("3. Scoreboard"), html.index("4. Broken signals"),
                          html.index("5. Shipped yesterday"))
    assert i1 < i2 < i3 < i4 < i5
    assert "Nothing needs you today." in html
    assert "All feeds reporting." in html
    assert "Nothing shipped yesterday." in html
    assert "https://x/brief?k=abc" in html          # the email links to the page
    assert "—" not in html                       # no em-dashes, house style


def test_render_needs_you_with_link_and_broken_fix_on_full_page_only():
    d = _data(needs_you=[{"kind": "sonar", "title": "comment @aipg", "url": "http://ig/1",
                          "detail": "suggested reply: yes", "age_h": 5.0}],
              broken_signals=[{"key": "tok", "text": "token dead", "fix": "mint a new one"}])
    email = CB.render_html(d, "https://x/brief?k=abc")
    page = CB.render_html(d, "https://x/brief?k=abc", full=True)
    assert "http://ig/1" in email and "token dead" in email
    assert "mint a new one" not in email and "mint a new one" in page
    assert "refresh" in page


def test_ai_spend_states():
    ctx = CB._Ctx()
    now = dt.datetime(2026, 11, 7, 7, 0, tzinfo=CB._CT)
    with mock.patch("services.llm_ledger.window_totals",
                    return_value={"day": "last 24h", "total_usd": 0.0, "calls": 0}), \
         mock.patch("services.llm_ledger.month_to_date_total", return_value=3.5):
        idle = CB._ai_spend(ctx, now, engine_activity=0)
        gap = CB._ai_spend(ctx, now, engine_activity=4)
    assert idle["state"] == "idle" and idle["mtd_usd"] == 3.5
    assert gap["state"] == "gap"
    assert any(b["key"] == "ledger-gap" for b in ctx.broken)
    with mock.patch("services.llm_ledger.window_totals",
                    return_value={"day": "last 24h", "total_usd": 1.25, "calls": 9}), \
         mock.patch("services.llm_ledger.month_to_date_total", return_value=3.5):
        assert CB._ai_spend(CB._Ctx(), now, engine_activity=0)["state"] == "metered"


def test_mrr_test_mode_key_is_a_broken_signal(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_" + "x" * 10)
    ctx = CB._Ctx()
    assert CB._mrr(ctx)["wired"] is False
    assert any("TEST mode" in b["text"] for b in ctx.broken)


def test_portal_unwired_is_a_broken_signal(monkeypatch):
    monkeypatch.delenv("PORTAL_BRIEF_URL", raising=False)
    monkeypatch.delenv("PORTAL_BRIEF_KEY", raising=False)
    ctx = CB._Ctx()
    assert CB._portal(ctx)["wired"] is False
    assert any(b["key"] == "portal-feed" for b in ctx.broken)


def test_shipped_dead_engine_signal_lists_token_fix():
    rows = [{"key": "aiphoneguy", "name": "AI Phone Guy", "signal_ok": False, "posts": [],
             "post_count": 0, "social": 0, "held": []}]
    ctx = CB._Ctx()
    with mock.patch("services.cmo_shipped.collect", return_value=rows), \
         mock.patch.object(CB, "fetch_all", return_value=[]):
        s = CB._shipped(ctx)
    assert s["brands"][0]["signal_ok"] is False
    assert any(b["key"] == "slipstream-gh-token" and "401" in b["text"] for b in ctx.broken)


def test_scoreboard_minor_only_when_moved():
    ctx = CB._Ctx()
    money = {"pipeline": {"wired": True, "by_brand": {
        "Book'd": {"wired": True, "pipeline_value": 5000, "deals_active": 2, "bookings_24h": 0, "closed_24h": 0},
        "WD": {"wired": True, "pipeline_value": 9000, "deals_active": 3, "bookings_24h": 1, "closed_24h": 0}}}}
    s = CB._scoreboard(ctx, {"brands": []}, money, {"wired": False}, {}, [])
    names = [m["name"] for m in s["majors"]]
    assert names == [n for _, n in CB.BUSINESSES]
    assert s["minors"] == []                                  # Book'd did not move
    money["pipeline"]["by_brand"]["Book'd"]["closed_24h"] = 1
    s = CB._scoreboard(CB._Ctx(), {"brands": []}, money, {"wired": False}, {}, [])
    assert [m["name"] for m in s["minors"]] == ["Book'd"]


def test_view_token_derives_from_admin_key(monkeypatch):
    monkeypatch.setenv("ADMIN_KEY", "secret-seed")
    tok = CB.view_token()
    assert len(tok) == 32 and CB.token_ok(tok) and not CB.token_ok("nope") and not CB.token_ok("")
    assert CB.page_url().endswith(f"/brief?k={tok}")
    monkeypatch.delenv("ADMIN_KEY")
    monkeypatch.setenv("API_KEYS", "")
    assert CB.token_ok("") is False


def test_send_skips_without_resend_key(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    assert CB.send(_data())["status"] == "skip"


def test_send_posts_one_email_to_one_inbox(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_" + "x" * 8)
    monkeypatch.delenv("CEO_BRIEF_TO", raising=False)
    captured = {}

    class R:
        ok = True
        headers = {"content-type": "application/json"}
        def json(self):
            return {"id": "em_1"}

    with mock.patch.object(CB.requests, "post", side_effect=lambda *a, **k: captured.update(k) or R()):
        res = CB.send(_data())
    assert res["status"] == "sent" and res["id"] == "em_1"
    assert captured["json"]["to"] == ["michael@automotiveintelligence.io"]
    assert "salesdroid" not in str(captured["json"]["to"])
    assert captured["json"]["subject"].startswith("CEO Brief")
    assert "text" in captured["json"] and "html" in captured["json"]


def test_run_daily_marks_ape_queue_only_after_a_confirmed_send(monkeypatch):
    data = _data(shipped={"brands": [], "ape": [{"id": 4, "persona": "Infrastructure", "summary": "rotated a cert"}]})
    marked = []
    with mock.patch.object(CB, "collect", return_value=data), \
         mock.patch.object(CB, "store_run"), \
         mock.patch.object(CB, "mark_ape_delivered", side_effect=lambda ids: marked.extend(ids)), \
         mock.patch.object(CB, "send", return_value={"status": "failed", "http": 500}):
        CB.run_daily()
    assert marked == []                                   # failed send keeps the ships queued
    with mock.patch.object(CB, "collect", return_value=data), \
         mock.patch.object(CB, "store_run"), \
         mock.patch.object(CB, "mark_ape_delivered", side_effect=lambda ids: marked.extend(ids)), \
         mock.patch.object(CB, "send", return_value={"status": "sent", "id": "em_2"}), \
         mock.patch("services.watchdog.record_heartbeat"):
        r = CB.run_daily()
    assert marked == [4] and r["status"] == "sent"


def test_pipeline_fully_unwired_is_a_broken_signal():
    ctx = CB._Ctx()
    with mock.patch("rivers.shared.morning_briefing.pull_revenue_pipeline_summary",
                    return_value={"wired": False, "by_brand": {}}):
        CB._pipeline(ctx)
    assert any(b["key"] == "twenty-pipeline" for b in ctx.broken)
