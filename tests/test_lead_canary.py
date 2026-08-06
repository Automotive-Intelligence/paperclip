"""Funnel standard #8 canary: responded-vs-answered scoring, loud fail, target select."""
from unittest import mock

from services import lead_canary as LC


def test_green_run_records_and_does_not_page():
    with mock.patch.object(LC, "_submit", return_value={"target": "ingest", "ack": {"ok": True}}), \
         mock.patch.object(LC, "_verify_durable",
                           return_value={"stored": True, "alerted": True, "status": "verified"}), \
         mock.patch.object(LC, "_record") as rec, \
         mock.patch.object(LC, "_alert_red") as page:
        r = LC.run_canary()
    assert r["ok"] is True and r["responded"] is True
    assert r["answered"].startswith("manual")          # scored SEPARATELY, never collapsed
    assert rec.called and not page.called              # recorded green, no page


def test_red_run_pages_loudly():
    # durable row missing -> the funnel would drop a real lead right now -> page.
    with mock.patch.object(LC, "_submit", return_value={"target": "form", "ack": {"ok": False}}), \
         mock.patch.object(LC, "_verify_durable",
                           return_value={"stored": False, "alerted": False, "status": None}), \
         mock.patch.object(LC, "_record"), \
         mock.patch.object(LC, "_alert_red", return_value=True) as page:
        r = LC.run_canary()
    assert r["ok"] is False and r["responded"] is False
    assert page.called and r["paged_on_fail"] is True


def test_stored_but_not_alerted_is_red():
    # fail closed: a row with no alert is NOT a pass (the whole point is a human is told).
    with mock.patch.object(LC, "_submit", return_value={"target": "ingest", "ack": {"ok": True}}), \
         mock.patch.object(LC, "_verify_durable",
                           return_value={"stored": True, "alerted": False, "status": "dead_letter"}), \
         mock.patch.object(LC, "_record"), \
         mock.patch.object(LC, "_alert_red", return_value=True) as page:
        r = LC.run_canary()
    assert r["responded"] is False and page.called


def test_submit_targets_real_form_when_configured():
    posted = {}
    class _R:
        status_code, ok, content = 200, True, b"{}"
        def json(self): return {"ok": True}
    def _fake_post(url, **kw):
        posted["url"], posted["hdr"], posted["body"] = url, kw["headers"], kw["json"]
        return _R()
    with mock.patch.dict("os.environ",
                         {"AIPG_CANARY_FORM_URL": "https://theaiphoneguy.ai/api/lead",
                          "LEAD_CANARY_SECRET": "s3cr3t"}), \
         mock.patch.object(LC.requests, "post", side_effect=_fake_post):
        out = LC._submit({"brand": "aipg", "synthetic": True, "idempotency_key": "canary-x"})
    assert out["target"] == "form"
    assert posted["url"].endswith("/api/lead")
    assert posted["hdr"]["x-canary-secret"] == "s3cr3t"      # secret gates synthetic injection
    assert posted["hdr"]["x-canary-key"] == "canary-x"       # explicit key -> verifiable row
    assert posted["body"]["synthetic"] is True


def test_submit_falls_back_to_ingest_core_when_no_form():
    with mock.patch.dict("os.environ", {"AIPG_CANARY_FORM_URL": "", "LEAD_CANARY_SECRET": ""}), \
         mock.patch("services.lead_store.ingest_lead", return_value={"ok": True, "stored": True}) as ing:
        out = LC._submit({"brand": "aipg", "synthetic": True, "idempotency_key": "canary-y"})
    assert out["target"] == "ingest" and ing.called


def test_green_streak_gate_ready_needs_real_form_and_zero_reds():
    # total, green, green_form, first, last
    with mock.patch.object(LC, "fetch_all", return_value=[(48, 48, 48, "t0", "t1")]):
        s = LC.green_streak("aipg", 48)
    assert s["all_green"] and s["gate_ready"] and s["reds"] == 0
    # a single red -> not gate-ready
    with mock.patch.object(LC, "fetch_all", return_value=[(48, 47, 30, "t0", "t1")]):
        s = LC.green_streak("aipg", 48)
    assert not s["all_green"] and not s["gate_ready"] and s["reds"] == 1
    # all green but NONE against the real form -> not gate-ready (ingest-only isn't #8)
    with mock.patch.object(LC, "fetch_all", return_value=[(48, 48, 0, "t0", "t1")]):
        s = LC.green_streak("aipg", 48)
    assert s["all_green"] and not s["gate_ready"]
