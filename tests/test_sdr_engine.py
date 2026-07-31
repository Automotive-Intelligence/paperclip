"""tests/test_sdr_engine.py -- unit tests for the SDR Engine
(services/sdr_engine.py). Network + CRM + GitHub seams are monkeypatched at
the module boundary throughout, per the plan
(docs/superpowers/plans/2026-07-27-sdr-engine-shadow.md) -- nothing here
touches the wire.
"""

from unittest.mock import patch

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


def test_reads_companies_filters_verified_junk_and_domainless(monkeypatch):
    fake_companies = [
        {"id": "1", "name": "Acme",
         "domainName": {"primaryLinkUrl": "https://acme.com"},
         "createdAt": "2026-07-20", "tags": []},
        # already gate-verified -> skip
        {"id": "2", "name": "Verified Co",
         "domainName": {"primaryLinkUrl": "https://verified.com"}, "tags": ["gate-verified"]},
        # no domain -> nothing to verify -> skip
        {"id": "3", "name": "No Domain Co", "domainName": None, "tags": []},
        # seeded test junk (name marker + example.com) -> skip
        {"id": "4", "name": "TWENTY-WRITER-PHASE1-SMOKE-TEST-DELETE-ME",
         "domainName": {"primaryLinkUrl": "https://example.com"}, "tags": []},
        # generic mailbox domain, not a business site -> skip
        {"id": "5", "name": "gmail.com",
         "domainName": {"primaryLinkUrl": "gmail.com"}, "tags": []},
        # our OWN brand domain -> never prospect ourselves -> skip
        {"id": "6", "name": "Automotive Intelligence",
         "domainName": {"primaryLinkUrl": "https://automotiveintelligence.io"}, "tags": []},
    ]
    monkeypatch.setattr("services.sdr_engine._twenty_get_companies", lambda rk, limit: fake_companies)
    out = read_unverified_candidates("callingdigital")
    assert len(out) == 1
    assert out[0]["twenty_id"] == "1"
    assert out[0]["company_name"] == "Acme"
    # scheme stripped so the gate's `https://{domain}` probe is well-formed
    assert out[0]["domain_on_file"] == "acme.com"
    assert out[0]["contact_name"] is None


def test_domain_on_file_scheme_is_stripped_for_the_gate(monkeypatch):
    # Twenty stores domains WITH a scheme; the gate prepends its own https://,
    # so a scheme left here becomes https://https://... and always fails.
    fake_companies = [
        {"id": "a", "name": "Bare", "domainName": {"primaryLinkUrl": "bare.com"}, "tags": []},
        {"id": "b", "name": "Https", "domainName": {"primaryLinkUrl": "https://scheme.com"}, "tags": []},
        {"id": "c", "name": "Path", "domainName": {"primaryLinkUrl": "https://has.com/mckinney"}, "tags": []},
        {"id": "d", "name": "Doubled", "domainName": {"primaryLinkUrl": "https://https://dbl.com"}, "tags": []},
    ]
    monkeypatch.setattr("services.sdr_engine._twenty_get_companies", lambda rk, limit: fake_companies)
    got = {c["twenty_id"]: c["domain_on_file"] for c in read_unverified_candidates("callingdigital")}
    assert got == {"a": "bare.com", "b": "scheme.com", "c": "has.com/mckinney", "d": "dbl.com"}


def test_shadow_mode_writes_nothing(monkeypatch):
    _candidates(monkeypatch, [{"twenty_id": "1", "company_name": "Acme", "domain_on_file": "acme.com", "tags": []}])
    monkeypatch.setattr(
        "services.sdr_engine._gate_run",
        lambda **k: {"verdict": "PASS", "queue_status": "auto_approved", "crm": None,
                      "reason": "ok", "confidence": 0.85},
    )
    wrote = []
    monkeypatch.setattr("services.sdr_engine._write_opportunity", lambda **k: (wrote.append(k), "created:x")[1])
    queued = []
    monkeypatch.setattr("services.sdr_engine._queue_approval", lambda **k: queued.append(k))
    out = run_sdr_engine("wd", commit=False)
    assert out["pass"] == 1 and out["written"] == 0
    assert wrote == [] and queued == []  # shadow: never writes anything


def test_commit_writes_opportunity_only_for_pass(monkeypatch):
    _candidates(monkeypatch, [
        {"twenty_id": "1", "company_name": "Pass", "domain_on_file": "p.com", "tags": []},
        {"twenty_id": "2", "company_name": "Fail", "domain_on_file": "f.com", "tags": []},
    ])

    def gate(**k):
        return {"verdict": "PASS", "queue_status": "auto_approved", "crm": None,
                 "reason": "ok", "confidence": 0.85} \
            if k["request"].entity["company_name"] == "Pass" \
            else {"verdict": "FAIL", "queue_status": None, "crm": None,
                  "reason": "real site elsewhere", "confidence": 0.0}

    monkeypatch.setattr("services.sdr_engine._gate_run", gate)
    wrote = []
    monkeypatch.setattr("services.sdr_engine._write_opportunity",
                        lambda **k: (wrote.append(k), "created:opp_1")[1])
    monkeypatch.setattr("services.sdr_engine.crm_ready", lambda rk: True)
    monkeypatch.setattr("services.sdr_engine._queue_approval", lambda **k: "auto_approved")
    monkeypatch.setattr("services.sdr_engine._commit_receipt", lambda path, body: None)
    out = run_sdr_engine("wd", commit=True)
    assert out["written"] == 1 and out["fail"] == 1
    # only the PASS company got an opportunity, keyed by its company id
    assert len(wrote) == 1 and wrote[0]["company_id"] == "1"


def test_commit_writes_opportunity_with_runtime_key_never_desk_key(monkeypatch):
    """Finding 1 (CRITICAL) regression: the opportunity write is keyed by the
    resolved runtime key ("callingdigital"), never the raw desk key ("wd")."""
    _candidates(monkeypatch, [
        {"twenty_id": "c1", "company_name": "Acme", "domain_on_file": "acme.com",
         "contact_name": "Pat", "contact_phone": "+15551230000", "contact_email": None, "tags": []},
    ])
    monkeypatch.setattr(
        "services.sdr_engine._gate_run",
        lambda **k: {"verdict": "PASS", "queue_status": "auto_approved", "crm": None,
                      "reason": "ok", "confidence": 0.85},
    )
    monkeypatch.setattr("services.sdr_engine.crm_ready", lambda rk: True)
    monkeypatch.setattr("services.sdr_engine._queue_approval", lambda **k: "auto_approved")
    monkeypatch.setattr("services.sdr_engine._commit_receipt", lambda path, body: None)

    calls = []
    monkeypatch.setattr("services.sdr_engine._write_opportunity",
                        lambda **k: (calls.append(k), "created:opp_x")[1])

    out = run_sdr_engine("wd", commit=True)

    assert len(calls) == 1
    assert calls[0]["runtime_key"] == "callingdigital"
    assert calls[0]["runtime_key"] not in ("wd", "ghl")
    assert calls[0]["company_id"] == "c1"
    assert out["written"] == 1


@pytest.mark.parametrize("desk_key,runtime_key", [
    ("wd", "callingdigital"),
    ("avi", "autointelligence"),
    ("bookd", "bookd"),
    # aipg deliberately excluded: GHL-backed, no Twenty workspace -- a default
    # source run refuses honestly before any write (see the refusal test).
])
def test_commit_never_uses_the_raw_desk_key(monkeypatch, desk_key, runtime_key):
    """Finding 1: no path passes a raw desk key into the opportunity write or
    the approval-queue write, for any Twenty-sourced brand."""
    _candidates(monkeypatch, [{"twenty_id": "c1", "company_name": "Acme", "domain_on_file": "acme.com", "tags": []}])
    monkeypatch.setattr(
        "services.sdr_engine._gate_run",
        lambda **k: {"verdict": "PASS", "queue_status": "auto_approved", "crm": None,
                      "reason": "ok", "confidence": 0.85},
    )
    monkeypatch.setattr("services.sdr_engine.crm_ready", lambda rk: True)
    wrote = []
    monkeypatch.setattr("services.sdr_engine._write_opportunity",
                        lambda **k: (wrote.append(k), "created:opp_x")[1])
    queued = []
    monkeypatch.setattr("services.sdr_engine._queue_approval",
                        lambda **k: (queued.append(k), "auto_approved")[1])
    monkeypatch.setattr("services.sdr_engine._commit_receipt", lambda path, body: None)

    run_sdr_engine(desk_key, commit=True)

    assert len(wrote) == 1
    assert wrote[0]["runtime_key"] == runtime_key
    assert wrote[0]["runtime_key"] not in ("wd", "avi", "aipg", "ghl")
    assert queued[0]["business_key"] == runtime_key


def test_commit_not_ready_queues_pending_never_writes(monkeypatch):
    """Finding 1: commit=True but crm_ready False -- the opportunity write must
    NOT fire; the candidate is queued pending instead of misrouted."""
    _candidates(monkeypatch, [{"twenty_id": "c1", "company_name": "Acme", "domain_on_file": "acme.com", "tags": []}])
    monkeypatch.setattr(
        "services.sdr_engine._gate_run",
        lambda **k: {"verdict": "PASS", "queue_status": "auto_approved", "crm": None,
                      "reason": "ok", "confidence": 0.85},
    )
    monkeypatch.setattr("services.sdr_engine.crm_ready", lambda rk: False)
    wrote = []
    monkeypatch.setattr("services.sdr_engine._write_opportunity", lambda **k: (wrote.append(k), "created:x")[1])
    queued = []
    monkeypatch.setattr("services.sdr_engine._queue_approval",
                        lambda **k: (queued.append(k), "pending_approval")[1])
    monkeypatch.setattr("services.sdr_engine._commit_receipt", lambda path, body: None)

    out = run_sdr_engine("wd", commit=True)

    assert wrote == []
    assert out["written"] == 0
    assert len(queued) == 1
    assert queued[0]["business_key"] == "callingdigital"
    assert queued[0]["risk_level"] == "medium"


def test_commit_dedup_skips_when_opportunity_exists(monkeypatch):
    """Dedup: a PASS company that already has an opportunity ("exists") is not
    re-created and is NOT counted as written; a fresh one ("created:") is.
    This is what keeps scheduled/repeat runs from duplicating."""
    _candidates(monkeypatch, [
        {"twenty_id": "fresh", "company_name": "Fresh", "domain_on_file": "fresh.com", "tags": []},
        {"twenty_id": "dupe", "company_name": "Dupe", "domain_on_file": "dupe.com", "tags": []},
    ])
    monkeypatch.setattr(
        "services.sdr_engine._gate_run",
        lambda **k: {"verdict": "PASS", "queue_status": "auto_approved", "crm": None,
                      "reason": "ok", "confidence": 0.85},
    )
    monkeypatch.setattr("services.sdr_engine.crm_ready", lambda rk: True)
    monkeypatch.setattr("services.sdr_engine._queue_approval", lambda **k: "auto_approved")
    monkeypatch.setattr("services.sdr_engine._commit_receipt", lambda path, body: None)
    monkeypatch.setattr(
        "services.sdr_engine._write_opportunity",
        lambda **k: "exists" if k["company_id"] == "dupe" else "created:opp_fresh",
    )

    out = run_sdr_engine("wd", commit=True)
    assert out["written"] == 1          # only the fresh one counts
    assert "dedup skip" in out["digest"]


def test_commit_write_failure_for_one_candidate_does_not_abort_the_batch(monkeypatch):
    """A transient failure in ONE candidate's write (here, _write_opportunity
    raising for candidate 2) must not abort the batch -- candidates before and
    after are still processed, the run returns with a digest, and the failed
    candidate is not counted as written."""
    _candidates(monkeypatch, [
        {"twenty_id": "1", "company_name": "First", "domain_on_file": "first.com", "tags": []},
        {"twenty_id": "2", "company_name": "Second", "domain_on_file": "second.com", "tags": []},
        {"twenty_id": "3", "company_name": "Third", "domain_on_file": "third.com", "tags": []},
    ])
    monkeypatch.setattr(
        "services.sdr_engine._gate_run",
        lambda **k: {"verdict": "PASS", "queue_status": "auto_approved", "crm": None,
                      "reason": "ok", "confidence": 0.85},
    )
    monkeypatch.setattr("services.sdr_engine.crm_ready", lambda rk: True)
    monkeypatch.setattr("services.sdr_engine._queue_approval", lambda **k: "auto_approved")
    monkeypatch.setattr("services.sdr_engine._commit_receipt", lambda path, body: None)

    seen = []

    def flaky_write(*, runtime_key, company_id, company_name, defect):
        seen.append(company_id)
        if company_id == "2":
            raise RuntimeError("Twenty POST timed out")
        return "created:opp_" + company_id

    monkeypatch.setattr("services.sdr_engine._write_opportunity", flaky_write)

    out = run_sdr_engine("wd", commit=True)

    assert seen == ["1", "2", "3"]          # all three attempted, none aborted the batch
    assert out["produced"] == 3
    assert "First" in out["digest"] and "Second" in out["digest"] and "Third" in out["digest"]
    assert out["digest_path"]
    assert "write-failed" in out["digest"]
    assert "twenty_id=2" in out["digest"]
    assert "Twenty POST timed out" in out["digest"]
    assert out["written"] == 2               # failed candidate not counted


def test_gate_failure_for_one_candidate_does_not_abort_the_batch(monkeypatch):
    """Code review round 3 (IMPORTANT, finding 1): the per-candidate
    try/except must ALSO cover the gate call itself, not just the write
    step -- _gate_run's curl probes/requests.get/llm_json call are DESIGNED
    to raise on a down/unreachable site. Candidate 2's _gate_run raises;
    candidates 1 and 3 must still be fully processed and the run must still
    return a complete digest. The errored candidate must not inflate any
    real verdict count."""
    _candidates(monkeypatch, [
        {"twenty_id": "1", "company_name": "First", "domain_on_file": "first.com", "tags": []},
        {"twenty_id": "2", "company_name": "Second", "domain_on_file": "second.com", "tags": []},
        {"twenty_id": "3", "company_name": "Third", "domain_on_file": "third.com", "tags": []},
    ])

    def flaky_gate(*, request):
        if request.entity["company_name"] == "Second":
            raise RuntimeError("site unreachable: connection reset")
        return {"verdict": "PASS", "queue_status": "auto_approved", "crm": None,
                "reason": "ok", "confidence": 0.85}

    monkeypatch.setattr("services.sdr_engine._gate_run", flaky_gate)
    monkeypatch.setattr("services.sdr_engine.crm_ready", lambda rk: True)
    wrote = []
    monkeypatch.setattr("services.sdr_engine._write_opportunity",
                        lambda **k: (wrote.append(k), "created:opp_x")[1])
    monkeypatch.setattr("services.sdr_engine._queue_approval", lambda **k: "auto_approved")
    monkeypatch.setattr("services.sdr_engine._commit_receipt", lambda path, body: None)

    out = run_sdr_engine("wd", commit=True)

    # Candidates 1 and 3 (the non-errored ones) still got their opportunity.
    assert len(wrote) == 2
    assert {w["company_name"] for w in wrote} == {"First", "Third"}

    assert out["errors"] == 1


def test_read_failure_yields_a_digest_not_an_exception(monkeypatch):
    """Code review round 3 (IMPORTANT, finding 1): if the upfront
    read_unverified_candidates() call itself raises (Twenty down, bad
    creds, etc.), run_sdr_engine must not propagate the exception -- it
    must still return a normal digest+counts result, with the failure
    honestly recorded and zero candidates."""
    def broken_read(rk, limit=100):
        raise RuntimeError("Twenty API 503")

    monkeypatch.setattr("services.sdr_engine.read_unverified_candidates", broken_read)
    monkeypatch.setattr("services.sdr_engine._commit_receipt", lambda path, body: None)

    out = run_sdr_engine("wd", commit=False)  # must not raise

    assert out["produced"] == 0
    assert out["pass"] == 0 and out["needs_human"] == 0 and out["fail"] == 0
    assert out["written"] == 0
    assert out["errors"] == 1
    assert "read failed" in out["digest"]
    assert "Twenty API 503" in out["digest"]
    assert out["digest_path"]


def test_aipg_source_unavailable_returns_honest_refusal(monkeypatch):
    """Code review round 3 (IMPORTANT, finding 2): aipg (aiphoneguy) is
    GHL-backed with no Twenty workspace, so the default twenty_unverified
    source can't serve it. run_sdr_engine must refuse honestly -- a normal
    digest+counts result stating the source isn't available, NOT the real
    tools/twenty.py ValueError ("no workspace mapping") escaping, and
    read_unverified_candidates must never even be called."""
    read_calls = []
    monkeypatch.setattr(
        "services.sdr_engine.read_unverified_candidates",
        lambda rk, limit=100: read_calls.append(rk) or [],
    )
    monkeypatch.setattr("services.sdr_engine._commit_receipt", lambda path, body: None)

    out = run_sdr_engine("aipg", commit=False)  # must not raise

    assert read_calls == []  # never even attempted the unconfigured Twenty read
    assert out["produced"] == 0
    assert out["written"] == 0
    assert out["errors"] == 0  # a known, advertised limitation -- not an error
    assert "not available" in out["digest"]
    assert "aipg" in out["digest"]
    assert out["digest_path"]

    # Same refusal in commit mode -- still no exception, still no read attempt.
    read_calls.clear()
    out_commit = run_sdr_engine("aipg", commit=True)
    assert read_calls == []
    assert "not available" in out_commit["digest"]


def test_digest_always_produced_publish_only_on_commit(monkeypatch):
    published = []
    monkeypatch.setattr("services.sdr_engine._commit_receipt", lambda path, body: published.append(path))
    from services.sdr_engine import _write_digest

    p_shadow = _write_digest("body", "wd", commit=False)
    p_commit = _write_digest("body", "wd", commit=True)
    assert p_shadow and p_commit          # a path/id is always returned
    assert len(published) == 1            # only the commit run published


def test_run_sdr_route_defaults_to_dry_run(monkeypatch):
    """POST /admin/run-sdr with no body must call run_sdr_engine("wd",
    commit=False) -- dry-run default, mirroring /admin/run-social."""
    from fastapi.testclient import TestClient

    import app as _app

    client = TestClient(_app.app)
    with patch("services.sdr_engine.run_sdr_engine", return_value={"produced": 0}) as mock_run:
        resp = client.post("/admin/run-sdr")
    assert resp.status_code == 200
    mock_run.assert_called_once_with("wd", commit=False)
