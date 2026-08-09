"""SP4 autonomous first touch: the no-queue contract under test.

Every guardrail must fail CLOSED and die as a digest exception -- never a
send, never a crash, never a queue. Dry-run must be a full evaluation that
writes nothing.
"""
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from services import sdr_first_touch as F

_CT = ZoneInfo("America/Chicago")
_TUE_10AM = datetime(2026, 8, 11, 10, 0, tzinfo=_CT)   # inside window
_SAT = datetime(2026, 8, 8, 10, 0, tzinfo=_CT)          # weekend

_OPP = {"id": "opp1", "companyId": "c1",
        "name": "Bel Air Partners, LLC | Website Rebuild (SDR-verified: pinch_zoom_blocked)"}


def _wire(monkeypatch, *, opps=None, email="info@belair.com", touched=False,
          scrutineer_block=False, suppressed=False, sends_today=0):
    # constrain to ONE brand so counts are deterministic
    monkeypatch.setattr(F, "_BRANDS", {"wd": ("callingdigital", "wd", "Worship Digital", True)})
    monkeypatch.setattr(F, "_sdr_opportunities", lambda rk: opps if opps is not None else [_OPP])
    monkeypatch.setattr(F, "_company_domain", lambda rk, cid: ("Bel Air Partners, LLC", "belair.com"))
    monkeypatch.setattr(F, "_already_touched", lambda rk, oid: touched)
    monkeypatch.setattr(F, "_published_email", lambda d: email)
    monkeypatch.setattr(F, "_suppressed", lambda e, b: suppressed)
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
    assert "WOULD SEND to info@belair.com" in out["digest"]


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
    monkeypatch.setattr(F, "_mark_touched", lambda rk, oid, em: marked.append(oid))
    out = F.run_first_touch(commit=True, now=_TUE_10AM)
    assert out["sent"] == 1 and len(fired) == 1
    assert fired[0]["to"] == "info@belair.com"
    assert fired[0]["from_identity"] == "wd"
    assert fired[0]["seat"] == "sdr_first_touch"
    assert marked == ["opp1"]


def test_every_guardrail_dies_as_exception_never_a_send(monkeypatch):
    cases = [
        dict(touched=True),                 # dedup -> skip (not exception)
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
