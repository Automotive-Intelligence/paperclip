"""SP4 autonomous first touch: the no-queue contract under test.

Every guardrail must fail CLOSED and die as a digest exception -- never a
send, never a crash, never a queue. Dry-run must be a full evaluation that
writes nothing.
"""
from datetime import date, datetime
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from services import sdr_first_touch as F

_CT = ZoneInfo("America/Chicago")
_TUE_10AM = datetime(2026, 8, 11, 10, 0, tzinfo=_CT)   # inside window
_SAT = datetime(2026, 8, 8, 10, 0, tzinfo=_CT)          # weekend

_OPP = {"id": "opp1", "companyId": "c1",
        "name": "Bel Air Partners, LLC | Website Rebuild (SDR-verified: pinch_zoom_blocked)"}


def _wire(monkeypatch, *, opps=None, email="info@belair.com", touch_state=(0, None),
          scrutineer_block=False, suppressed=False, sends_today=0, replied=False):
    # constrain to ONE brand so counts are deterministic
    monkeypatch.setattr(F, "_BRANDS", {"wd": ("callingdigital", "wd", "Worship Digital", True)})
    monkeypatch.setattr(F, "_sdr_opportunities", lambda rk: opps if opps is not None else [_OPP])
    monkeypatch.setattr(F, "_company_domain", lambda rk, cid: ("Bel Air Partners, LLC", "belair.com"))
    monkeypatch.setattr(F, "_touch_state", lambda rk, oid: touch_state)
    monkeypatch.setattr(F, "_published_email", lambda d: email)
    monkeypatch.setattr(F, "_suppressed", lambda e, b: suppressed)
    monkeypatch.setattr(F, "_has_replied", lambda desk, em, since: replied)
    monkeypatch.setattr(F, "_scrutineer",
                        lambda *a, **k: (scrutineer_block, "generic" if scrutineer_block else "ok"))
    monkeypatch.setattr(F, "_sends_today", lambda ident: sends_today)
    monkeypatch.setattr(F, "_mark_touched", lambda *a, **k: None)


def test_compose_validate_round_trip_is_clean():
    s, b = F.compose("Bel Air Partners, LLC", "belair.com", "pinch_zoom_blocked",
                     "Automotive Intelligence")
    assert F.validate(s, b, company_name="Bel Air Partners, LLC", domain="belair.com",
                      defect_kind="pinch_zoom_blocked", brand_name="Automotive Intelligence") is None
    assert "pinch-to-zoom" in b
    assert "no thanks" in b               # the opt-out promise
    assert "—" not in b              # no em-dash
    assert "$" not in b and "price" not in b.lower()


def test_validator_catches_copy_drift_and_forbidden_slots():
    s, b = F.compose("Acme", "acme.com", "slow_load", "Worship Digital")
    assert F.validate(s, b + " special price today!", company_name="Acme",
                      domain="acme.com", defect_kind="slow_load",
                      brand_name="Worship Digital") == "copy_drift"
    assert F.validate(s, b, company_name="Acme $99 deal", domain="acme.com",
                      defect_kind="slow_load", brand_name="Worship Digital") is not None


def test_dry_run_full_evaluation_sends_nothing(monkeypatch):
    _wire(monkeypatch)
    fired = []
    monkeypatch.setattr("tools.brand_send.send_as_brand", lambda **k: fired.append(k))
    out = F.run_first_touch(commit=False, now=_TUE_10AM)
    assert fired == []
    assert out["sent"] == 0 and out["considered"] == 1
    assert "WOULD SEND touch 1 to info@belair.com" in out["digest"]


def test_kill_switch_blocks_commit_sends(monkeypatch):
    _wire(monkeypatch)
    monkeypatch.delenv("SDR_FIRST_TOUCH_ENABLED", raising=False)
    fired = []
    monkeypatch.setattr("tools.brand_send.send_as_brand", lambda **k: fired.append(k))
    out = F.run_first_touch(commit=True, now=_TUE_10AM)
    assert fired == []
    assert "kill_switch_off" in out["digest"]


def test_commit_sends_once_and_marks_touched(monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setenv("SDR_FIRST_TOUCH_ENABLED", "1")
    fired, marked = [], []
    monkeypatch.setattr(
        "tools.brand_send.send_as_brand",
        lambda **k: fired.append(k) or SimpleNamespace(sent=True, outcome="sent"))
    monkeypatch.setattr(F, "_mark_touched", lambda rk, oid, em, touch_number=1: marked.append((oid, touch_number)))
    out = F.run_first_touch(commit=True, now=_TUE_10AM)
    assert out["sent"] == 1 and len(fired) == 1
    assert fired[0]["to"] == "info@belair.com"
    assert fired[0]["from_identity"] == "wd"
    assert fired[0]["seat"] == "sdr_first_touch"
    assert marked == [("opp1", 1)]


def test_every_guardrail_dies_as_exception_never_a_send(monkeypatch):
    cases = [
        dict(touch_state=(F.MAX_TOUCHES, date(2026, 8, 1))),  # sequence complete -> skip
        dict(email=None),                   # no verified email
        dict(suppressed=True),              # DNC
        dict(scrutineer_block=True),        # gate BLOCK
        dict(sends_today=F.DAILY_CAP_PER_BRAND),  # cap
    ]
    for kw in cases:
        _wire(monkeypatch, **kw)
        monkeypatch.setenv("SDR_FIRST_TOUCH_ENABLED", "1")
        fired = []
        monkeypatch.setattr(
            "tools.brand_send.send_as_brand",
            lambda **k: fired.append(k) or SimpleNamespace(sent=True, outcome="sent"))
        out = F.run_first_touch(commit=True, now=_TUE_10AM)
        assert fired == [], f"guardrail leaked a send for {kw}"
        assert out["sent"] == 0


def test_brand_motion_mismatch_blocks_non_wd_rebuild_sends(monkeypatch):
    # The rebuild pitch is WD's motion; an AvI-identity rebuild email is a
    # brand-scope violation and must die as an exception.
    _wire(monkeypatch)
    monkeypatch.setattr(F, "_BRANDS",
                        {"avi": ("autointelligence", "avi", "Automotive Intelligence", False)})
    monkeypatch.setenv("SDR_FIRST_TOUCH_ENABLED", "1")
    fired = []
    monkeypatch.setattr(
        "tools.brand_send.send_as_brand",
        lambda **k: fired.append(k) or SimpleNamespace(sent=True, outcome="sent"))
    out = F.run_first_touch(commit=True, now=_TUE_10AM)
    assert fired == []
    assert "brand_motion_mismatch" in out["digest"]


def test_weekend_is_outside_window(monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setenv("SDR_FIRST_TOUCH_ENABLED", "1")
    out = F.run_first_touch(commit=True, now=_SAT)
    assert out["sent"] == 0
    assert "outside_window" in out["digest"]


def test_scrutineer_fails_closed_on_scorer_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("llm down")
    monkeypatch.setattr("services.studio_social_llm.llm_json", boom)
    blocked, why = F._scrutineer("s", "b", "Acme", "acme.com", "slow_load")
    assert blocked is True
    assert "scorer_down_block" in why


def test_cap_check_fails_closed_when_audit_store_unreachable(monkeypatch):
    def boom(ident):
        raise RuntimeError("db down")
    monkeypatch.setattr(F, "_sends_today", boom)
    assert F._sends_today_safe("avi") == F.DAILY_CAP_PER_BRAND


# ---- follow-up sequence (2026-08-11) --------------------------------------

def test_touch_2_and_3_compose_no_new_fabricated_claim(monkeypatch):
    for n in (2, 3):
        s, b = F.compose("Bel Air Partners, LLC", "belair.com", "pinch_zoom_blocked",
                         "Worship Digital", touch_number=n)
        assert F.validate(s, b, company_name="Bel Air Partners, LLC", domain="belair.com",
                          defect_kind="pinch_zoom_blocked", brand_name="Worship Digital",
                          touch_number=n) is None
        assert "pinch-to-zoom" in b  # same real evidence, never a new one
        assert "—" not in b
        assert "$" not in b and "price" not in b.lower()
    assert "Last one from me" in F.compose("A", "a.com", "slow_load", "WD", 3)[1]


def test_touch_2_not_due_before_the_gap(monkeypatch):
    # touch 1 sent yesterday; touch 2 needs >=2 days -- must not fire yet.
    _wire(monkeypatch, touch_state=(1, date(2026, 8, 10)))
    monkeypatch.setenv("SDR_FIRST_TOUCH_ENABLED", "1")
    fired = []
    monkeypatch.setattr("tools.brand_send.send_as_brand",
                        lambda **k: fired.append(k) or SimpleNamespace(sent=True, outcome="sent"))
    out = F.run_first_touch(commit=True, now=_TUE_10AM)  # "today" = 2026-08-11
    assert fired == []
    assert "not due yet" in out["digest"]


def test_touch_2_fires_after_the_gap_when_no_reply(monkeypatch):
    # touch 1 sent 3 days ago (>=2 required), no reply -> touch 2 fires.
    _wire(monkeypatch, touch_state=(1, date(2026, 8, 8)), replied=False)
    monkeypatch.setenv("SDR_FIRST_TOUCH_ENABLED", "1")
    fired = []
    monkeypatch.setattr("tools.brand_send.send_as_brand",
                        lambda **k: fired.append(k) or SimpleNamespace(sent=True, outcome="sent"))
    out = F.run_first_touch(commit=True, now=_TUE_10AM)
    assert len(fired) == 1 and out["sent"] == 1
    assert "Last one from me" not in fired[0]["body"]  # this is touch 2, not touch 3
    assert "touch 2" in out["digest"]


def test_reply_stops_the_sequence_never_sends_the_next_touch(monkeypatch):
    _wire(monkeypatch, touch_state=(1, date(2026, 8, 8)), replied=True)
    monkeypatch.setenv("SDR_FIRST_TOUCH_ENABLED", "1")
    fired = []
    monkeypatch.setattr("tools.brand_send.send_as_brand",
                        lambda **k: fired.append(k) or SimpleNamespace(sent=True, outcome="sent"))
    out = F.run_first_touch(commit=True, now=_TUE_10AM)
    assert fired == []
    assert out["sent"] == 0
    assert "replied" in out["digest"]


def test_has_replied_fails_closed_on_gmail_error(monkeypatch):
    def boom(desk, query, limit=5):
        raise RuntimeError("gmail api down")
    monkeypatch.setattr("tools.gmail_multi.search", boom)
    assert F._has_replied("wd", "prospect@x.com", date(2026, 8, 1)) is True


def test_has_replied_true_when_a_thread_is_found(monkeypatch):
    monkeypatch.setattr("tools.gmail_multi.search", lambda desk, q, limit=5: [{"id": "t1"}])
    assert F._has_replied("wd", "prospect@x.com", date(2026, 8, 1)) is True


def test_has_replied_false_when_inbox_is_silent(monkeypatch):
    monkeypatch.setattr("tools.gmail_multi.search", lambda desk, q, limit=5: [])
    assert F._has_replied("wd", "prospect@x.com", date(2026, 8, 1)) is False


def test_touch_state_read_failure_fails_closed_to_max_touches(monkeypatch):
    import requests as req_mod

    class _Resp:
        ok = False
    monkeypatch.setattr(F, "_twenty", lambda rk: ("https://x", {}))
    monkeypatch.setattr(req_mod, "get", lambda *a, **k: _Resp())
    n, d = F._touch_state("callingdigital", "opp1")
    assert n == F.MAX_TOUCHES
    assert d is None


def test_sequence_completes_after_touch_3(monkeypatch):
    _wire(monkeypatch, touch_state=(F.MAX_TOUCHES, date(2026, 8, 1)))
    monkeypatch.setenv("SDR_FIRST_TOUCH_ENABLED", "1")
    fired = []
    monkeypatch.setattr("tools.brand_send.send_as_brand",
                        lambda **k: fired.append(k) or SimpleNamespace(sent=True, outcome="sent"))
    out = F.run_first_touch(commit=True, now=_TUE_10AM)
    assert fired == []
    assert "sequence complete" in out["digest"]


# --------------------------------------------------------------------- _published_email discovery
# 2026-08-14: live-verified against production Twenty. Wyndham Custom Homes,
# the ONE eligible WD candidate in the entire system, was failing
# no_verified_email -- its real site (checked live) publishes no email at
# all on homepage or /contact-us, only a contact form. These tests prove the
# widened discovery (more guessed paths + homepage-nav link crawl) finds
# real emails a fixed 4-path guess list missed, while a genuinely
# email-less, form-only site (Wyndham's real shape) still correctly
# returns None -- never inventing a broker/guessed address.

def _resp(html="", ok=True):
    return SimpleNamespace(ok=ok, text=html)


def test_finds_email_on_a_newly_added_guess_path(monkeypatch):
    # /team wasn't checked before this fix; the homepage and old guesses
    # have nothing.
    def fake_get(url, **kw):
        if url == "https://acme.com/team":
            return _resp("<p>Reach us: hello@acme.com</p>")
        return _resp("<p>no contact info here</p>")
    monkeypatch.setattr(F.requests, "get", fake_get)
    assert F._published_email("acme.com") == "hello@acme.com"


def test_finds_email_via_discovered_nav_link_not_in_fixed_guesses(monkeypatch):
    # A CMS-specific path ("/get-in-touch") that isn't in the fixed guess
    # list, but IS linked from the homepage nav with contact-hinting text.
    home = '<nav><a href="/get-in-touch">Get In Touch</a></nav>'

    def fake_get(url, **kw):
        if url == "https://acme.com":
            return _resp(home)
        if url == "https://acme.com/get-in-touch":
            return _resp("<p>Email owner@acme.com anytime</p>")
        return _resp("no email", ok=True)
    monkeypatch.setattr(F.requests, "get", fake_get)
    assert F._published_email("acme.com") == "owner@acme.com"


def test_form_only_site_with_no_published_email_returns_none():
    # Wyndham's real shape: homepage links to a contact page, the contact
    # page 200s, but neither page publishes an email -- only a <form>.
    home = '<nav><a href="https://wyndhamcustomhomes.com/contact-us/">Contact Us</a></nav>'
    contact = '<form action="/submit"><input name="email"></form>'

    def fake_get(url, **kw):
        if url == "https://wyndhamcustomhomes.com":
            return _resp(home)
        if "contact" in url:
            return _resp(contact)
        return _resp("", ok=False)
    with mock.patch.object(F.requests, "get", side_effect=fake_get):
        assert F._published_email("wyndhamcustomhomes.com") is None


def test_junk_local_parts_still_excluded():
    def fake_get(url, **kw):
        if url == "https://acme.com":
            return _resp("<p>webmaster@acme.com or postmaster@acme.com only</p>")
        return _resp("", ok=False)
    with mock.patch.object(F.requests, "get", side_effect=fake_get):
        assert F._published_email("acme.com") is None


def test_wrong_domain_email_never_returned():
    def fake_get(url, **kw):
        if url == "https://acme.com":
            return _resp("<p>Support widget by help@othervendor.com</p>")
        return _resp("", ok=False)
    with mock.patch.object(F.requests, "get", side_effect=fake_get):
        assert F._published_email("acme.com") is None


def test_discovered_links_capped_and_deduped():
    home = "".join(f'<a href="/contact-{i}">Contact {i}</a>' for i in range(10))
    home += '<a href="/contact-0">Contact again</a>'
    links = F._discover_contact_links(home, "acme.com")
    assert len(links) == F._MAX_DISCOVERED_LINKS
    assert len(set(links)) == len(links)
