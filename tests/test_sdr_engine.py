"""tests/test_sdr_engine.py -- unit tests for the SDR Engine
(services/sdr_engine.py). Network + CRM + GitHub seams are monkeypatched at
the module boundary throughout, per the plan
(docs/superpowers/plans/2026-07-27-sdr-engine-shadow.md) -- nothing here
touches the wire.
"""

import pytest

from services.sdr_engine import (
    crm_ready,
    read_unverified_candidates,
    resolve_business_key,
    run_sdr_engine,
)


def _candidates(monkeypatch, cands):
    monkeypatch.setattr("services.sdr_engine.read_unverified_candidates", lambda rk, limit=100: cands)


def test_desk_keys_map_to_runtime_keys():
    assert resolve_business_key("wd") == "callingdigital"
    assert resolve_business_key("avi") == "autointelligence"
    assert resolve_business_key("aipg") == "aiphoneguy"
    assert resolve_business_key("bookd") == "bookd"


def test_unknown_desk_key_raises_not_silently_defaults():
    with pytest.raises(ValueError):
        resolve_business_key("nope")


def test_crm_ready_reads_real_readiness_flags(monkeypatch):
    # callingdigital -> twenty -> twenty_ready_for_business -> twenty_wd_ready,
    # which config/runtime.py derives straight off TWENTY_WD_API_KEY.
    monkeypatch.setenv("TWENTY_WD_API_KEY", "shadow-test-key")
    from config import runtime

    runtime.get_settings.cache_clear()
    try:
        assert crm_ready("callingdigital") is True
    finally:
        monkeypatch.delenv("TWENTY_WD_API_KEY", raising=False)
        runtime.get_settings.cache_clear()


def test_crm_not_ready_when_unconfigured(monkeypatch):
    monkeypatch.delenv("TWENTY_AVI_API_KEY", raising=False)
    monkeypatch.delenv("TWENTY_AVI_API_URL", raising=False)
    from config import runtime

    runtime.get_settings.cache_clear()
    try:
        assert crm_ready("autointelligence") is False
    finally:
        runtime.get_settings.cache_clear()


def test_reads_and_filters_out_already_verified(monkeypatch):
    fake_people = [
        {"id": "1", "name": {"firstName": "A"}, "companyName": "Acme",
         "domainName": {"primaryLinkUrl": "acme.com"},
         "emails": {"primaryEmail": "a@acme.com"}, "phones": {"primaryPhoneNumber": "+1555"},
         "createdAt": "2026-07-20", "tags": []},
        {"id": "2", "name": {"firstName": "B"}, "companyName": "Verified Co", "tags": ["gate-verified"]},
    ]
    monkeypatch.setattr("services.sdr_engine._twenty_get_people", lambda rk, limit: fake_people)
    out = read_unverified_candidates("callingdigital")
    assert len(out) == 1 and out[0]["twenty_id"] == "1" and out[0]["domain_on_file"] == "acme.com"


def test_shadow_mode_writes_nothing(monkeypatch):
    _candidates(monkeypatch, [{"twenty_id": "1", "company_name": "Acme", "domain_on_file": "acme.com"}])
    monkeypatch.setattr(
        "services.sdr_engine._gate_run",
        lambda **k: {"verdict": "PASS", "queue_status": "auto_approved", "crm": None, "reason": "ok"},
    )
    pushed = []
    monkeypatch.setattr("services.sdr_engine._push_crm", lambda **k: pushed.append(k))
    out = run_sdr_engine("wd", commit=False)
    assert out["pass"] == 1 and out["written"] == 0 and pushed == []  # shadow: never writes


def test_commit_writes_only_pass(monkeypatch):
    _candidates(monkeypatch, [
        {"twenty_id": "1", "company_name": "Pass", "domain_on_file": "p.com"},
        {"twenty_id": "2", "company_name": "Fail", "domain_on_file": "f.com"},
    ])

    def gate(**k):
        return {"verdict": "PASS", "queue_status": "auto_approved", "crm": None, "reason": "ok"} \
            if k["request"].entity["company_name"] == "Pass" \
            else {"verdict": "FAIL", "queue_status": None, "crm": None, "reason": "real site elsewhere"}

    monkeypatch.setattr("services.sdr_engine._gate_run", gate)
    pushed = []
    monkeypatch.setattr("services.sdr_engine._push_crm", lambda **k: pushed.append(k))
    monkeypatch.setattr("services.sdr_engine.crm_ready", lambda rk: True)
    # A commit=True run also publishes its digest receipt (Task 4); stub the
    # publish seam so this unit test never hits the network regardless of
    # ambient SLIPSTREAM_GH_TOKEN state.
    monkeypatch.setattr("services.sdr_engine._commit_receipt", lambda path, body: None)
    out = run_sdr_engine("wd", commit=True)
    assert out["written"] == 1 and out["fail"] == 1
    assert len(pushed) == 1 and pushed[0]["business_key"] == "callingdigital"


def test_digest_always_produced_publish_only_on_commit(monkeypatch):
    published = []
    monkeypatch.setattr("services.sdr_engine._commit_receipt", lambda path, body: published.append(path))
    from services.sdr_engine import _write_digest

    p_shadow = _write_digest("body", "wd", commit=False)
    p_commit = _write_digest("body", "wd", commit=True)
    assert p_shadow and p_commit          # a path/id is always returned
    assert len(published) == 1            # only the commit run published
