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
    pushed = []
    monkeypatch.setattr("services.sdr_engine._push_crm", lambda **k: pushed.append(k))
    queued = []
    monkeypatch.setattr("services.sdr_engine._queue_approval", lambda **k: queued.append(k))
    tagged = []
    monkeypatch.setattr("services.sdr_engine._tag_verified", lambda **k: tagged.append(k))
    out = run_sdr_engine("wd", commit=False)
    assert out["pass"] == 1 and out["written"] == 0
    assert pushed == [] and queued == [] and tagged == []  # shadow: never writes anything


def test_commit_writes_only_pass(monkeypatch):
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
    pushed = []
    monkeypatch.setattr("services.sdr_engine._push_crm", lambda **k: pushed.append(k))
    monkeypatch.setattr("services.sdr_engine.crm_ready", lambda rk: True)
    monkeypatch.setattr("services.sdr_engine._queue_approval", lambda **k: "auto_approved")
    monkeypatch.setattr("services.sdr_engine._tag_verified", lambda **k: None)
    # A commit=True run also publishes its digest receipt (Task 4); stub the
    # publish seam so this unit test never hits the network regardless of
    # ambient SLIPSTREAM_GH_TOKEN state.
    monkeypatch.setattr("services.sdr_engine._commit_receipt", lambda path, body: None)
    out = run_sdr_engine("wd", commit=True)
    assert out["written"] == 1 and out["fail"] == 1
    assert len(pushed) == 1 and pushed[0]["business_key"] == "callingdigital"


def test_commit_pushes_with_runtime_key_never_desk_key_or_ghl_default(monkeypatch):
    """Finding 1 (CRITICAL) regression: the real crm_router.push_prospects_to_crm
    must be called EXACTLY ONCE, keyed by the resolved runtime key
    ("callingdigital"), never the raw desk key ("wd") and never let
    resolve_crm_provider's business_crm_map.get(..., "ghl") default fire."""
    _candidates(monkeypatch, [
        {"twenty_id": "1", "company_name": "Acme", "domain_on_file": "acme.com",
         "contact_name": "Pat", "contact_phone": "+15551230000", "contact_email": None, "tags": []},
    ])
    monkeypatch.setattr(
        "services.sdr_engine._gate_run",
        lambda **k: {"verdict": "PASS", "queue_status": "auto_approved", "crm": None,
                      "reason": "ok", "confidence": 0.85},
    )
    monkeypatch.setattr("services.sdr_engine.crm_ready", lambda rk: True)
    monkeypatch.setattr("services.sdr_engine._queue_approval", lambda **k: "auto_approved")
    monkeypatch.setattr("services.sdr_engine._tag_verified", lambda **k: None)
    monkeypatch.setattr("services.sdr_engine._commit_receipt", lambda path, body: None)

    calls = []

    def fake_push(prospects, source_agent, business_key):
        calls.append({"prospects": prospects, "source_agent": source_agent, "business_key": business_key})
        return ("twenty", [{"status": "created"}])

    monkeypatch.setattr("tools.crm_router.push_prospects_to_crm", fake_push)

    out = run_sdr_engine("wd", commit=True)

    assert len(calls) == 1
    assert calls[0]["business_key"] == "callingdigital"
    assert calls[0]["business_key"] not in ("wd", "ghl")
    assert out["written"] == 1


@pytest.mark.parametrize("desk_key,runtime_key", [
    ("wd", "callingdigital"),
    ("avi", "autointelligence"),
    ("bookd", "bookd"),
    # aipg deliberately excluded here: it is GHL-backed with no Twenty
    # workspace, so a default-source run refuses honestly before ever
    # reaching read_unverified_candidates/_push_crm (finding 2, round 3) --
    # see test_aipg_source_unavailable_returns_honest_refusal below.
])
def test_commit_never_pushes_the_raw_desk_key(monkeypatch, desk_key, runtime_key):
    """Finding 1: no path passes a raw desk key ("wd"/"avi"/"bookd") into
    crm_router, for any Twenty-sourced brand."""
    _candidates(monkeypatch, [{"twenty_id": "1", "company_name": "Acme", "domain_on_file": "acme.com", "tags": []}])
    monkeypatch.setattr(
        "services.sdr_engine._gate_run",
        lambda **k: {"verdict": "PASS", "queue_status": "auto_approved", "crm": None,
                      "reason": "ok", "confidence": 0.85},
    )
    monkeypatch.setattr("services.sdr_engine.crm_ready", lambda rk: True)
    pushed = []
    monkeypatch.setattr("services.sdr_engine._push_crm", lambda **k: pushed.append(k))
    monkeypatch.setattr("services.sdr_engine._queue_approval", lambda **k: "auto_approved")
    monkeypatch.setattr("services.sdr_engine._tag_verified", lambda **k: None)
    monkeypatch.setattr("services.sdr_engine._commit_receipt", lambda path, body: None)

    run_sdr_engine(desk_key, commit=True)

    assert len(pushed) == 1
    assert pushed[0]["business_key"] == runtime_key
    assert pushed[0]["business_key"] not in ("wd", "avi", "aipg", "ghl")


def test_commit_not_ready_queues_pending_never_pushes(monkeypatch):
    """Finding 1: commit=True but crm_ready False for that brand -- push
    must NOT fire; the candidate is queued pending instead of misrouted."""
    _candidates(monkeypatch, [{"twenty_id": "1", "company_name": "Acme", "domain_on_file": "acme.com", "tags": []}])
    monkeypatch.setattr(
        "services.sdr_engine._gate_run",
        lambda **k: {"verdict": "PASS", "queue_status": "auto_approved", "crm": None,
                      "reason": "ok", "confidence": 0.85},
    )
    monkeypatch.setattr("services.sdr_engine.crm_ready", lambda rk: False)
    pushed = []
    monkeypatch.setattr("services.sdr_engine._push_crm", lambda **k: pushed.append(k))
    queued = []
    monkeypatch.setattr("services.sdr_engine._queue_approval",
                        lambda **k: (queued.append(k), "pending_approval")[1])
    monkeypatch.setattr("services.sdr_engine._tag_verified", lambda **k: None)
    monkeypatch.setattr("services.sdr_engine._commit_receipt", lambda path, body: None)

    out = run_sdr_engine("wd", commit=True)

    assert pushed == []
    assert out["written"] == 0
    assert len(queued) == 1
    assert queued[0]["business_key"] == "callingdigital"
    assert queued[0]["risk_level"] == "medium"


def test_commit_tags_processed_candidates_shadow_never_tags(monkeypatch):
    """Finding 2 (IMPORTANT): commit mode tags every processed (pushed or
    queued) candidate so the next run's dedup skips it; shadow never tags
    (tagging is itself a write, and shadow must stay side-effect-free)."""
    _candidates(monkeypatch, [
        {"twenty_id": "1", "company_name": "Pass", "domain_on_file": "p.com", "tags": []},
        {"twenty_id": "2", "company_name": "Human", "domain_on_file": "h.com", "tags": ["existing"]},
    ])

    def gate(**k):
        name = k["request"].entity["company_name"]
        if name == "Pass":
            return {"verdict": "PASS", "queue_status": "auto_approved", "crm": None,
                    "reason": "ok", "confidence": 0.85}
        return {"verdict": "NEEDS_HUMAN", "queue_status": "pending_approval", "crm": None,
                "reason": "contact unverified", "confidence": 0.5}

    monkeypatch.setattr("services.sdr_engine._gate_run", gate)
    monkeypatch.setattr("services.sdr_engine.crm_ready", lambda rk: True)
    monkeypatch.setattr("services.sdr_engine._push_crm", lambda **k: None)
    monkeypatch.setattr("services.sdr_engine._queue_approval", lambda **k: "auto_approved")
    monkeypatch.setattr("services.sdr_engine._commit_receipt", lambda path, body: None)

    tagged = []
    monkeypatch.setattr("services.sdr_engine._tag_verified", lambda **k: tagged.append(k))

    run_sdr_engine("wd", commit=True)
    assert len(tagged) == 2
    assert {t["twenty_id"] for t in tagged} == {"1", "2"}
    assert {t["runtime_key"] for t in tagged} == {"callingdigital"}
    # the existing "existing" tag must be threaded through, not dropped
    human_tag_call = next(t for t in tagged if t["twenty_id"] == "2")
    assert human_tag_call["existing_tags"] == ["existing"]

    # Shadow: same candidates, same gate verdicts -- tag-write must never fire.
    tagged.clear()
    run_sdr_engine("wd", commit=False)
    assert tagged == []


def test_commit_write_failure_for_one_candidate_does_not_abort_the_batch(monkeypatch):
    """Code review round 2 (IMPORTANT): a transient failure anywhere in ONE
    candidate's write sequence (here, _tag_verified raising for the 2nd
    candidate) must not abort the whole run -- candidates before and after
    it are still fully processed, the run returns normally with a digest,
    and the failed candidate is not counted as written."""
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

    pushed = []
    monkeypatch.setattr("services.sdr_engine._push_crm", lambda **k: pushed.append(k))

    def flaky_tag(*, runtime_key, twenty_id, existing_tags):
        if twenty_id == "2":
            raise RuntimeError("Twenty PATCH timed out")

    monkeypatch.setattr("services.sdr_engine._tag_verified", flaky_tag)

    out = run_sdr_engine("wd", commit=True)

    # All 3 candidates reached the push step -- the failure was isolated to
    # candidate 2's tag write, not the whole batch.
    assert len(pushed) == 3

    # The run completed normally and still produced a digest/receipt --
    # nothing was silently dropped.
    assert out["produced"] == 3
    assert "First" in out["digest"]
    assert "Second" in out["digest"]
    assert "Third" in out["digest"]
    assert out["digest_path"]

    # The digest honestly records the failure (candidate id + exception).
    assert "write-failed" in out["digest"]
    assert "twenty_id=2" in out["digest"]
    assert "Twenty PATCH timed out" in out["digest"]

    # The failed candidate is not counted as written; the other two are.
    assert out["written"] == 2


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
    pushed = []
    monkeypatch.setattr("services.sdr_engine._push_crm", lambda **k: pushed.append(k))
    monkeypatch.setattr("services.sdr_engine._queue_approval", lambda **k: "auto_approved")
    monkeypatch.setattr("services.sdr_engine._tag_verified", lambda **k: None)
    monkeypatch.setattr("services.sdr_engine._commit_receipt", lambda path, body: None)

    out = run_sdr_engine("wd", commit=True)

    # Candidates 1 and 3 (the non-errored ones) were still fully pushed.
    assert len(pushed) == 2
    assert {p["prospects"][0]["business_name"] for p in pushed} == {"First", "Third"}

    # The run completed normally with a full digest -- nothing silently lost.
    assert out["produced"] == 3
    assert "First" in out["digest"]
    assert "Second" in out["digest"]
    assert "Third" in out["digest"]
    assert out["digest_path"]

    # The errored candidate is reported honestly but does NOT inflate any
    # real verdict count -- it's tallied separately.
    assert "verify-failed" in out["digest"]
    assert "connection reset" in out["digest"]
    assert out["pass"] == 2          # only the 2 genuinely-gated candidates
    assert out["needs_human"] == 0
    assert out["fail"] == 0
    assert out["written"] == 2
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
