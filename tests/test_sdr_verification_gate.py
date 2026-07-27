"""tests/test_sdr_verification_gate.py -- unit tests for the SDR Verification
Gate (services/sdr_verification_gate.py). Each check is exercised in
isolation via monkeypatch on the module's network seams (_probe_headers,
_fetch_html, _ttfb) so nothing touches the wire. See
docs/superpowers/plans/2026-07-27-sdr-verification-gate.md for the plan this
implements task-by-task, and
docs/superpowers/specs/2026-07-27-sdr-verification-gate-design.md for the
contract.
"""

from services.sdr_verification_gate import (
    VerificationRequest,
    VerificationResult,
    check_contact,
    check_defect,
    resolve_primary_site,
    run,
    verify,
)


def test_cert_cn_mismatch_resolves_to_real_host(monkeypatch):
    # Bonick case: domain_on_file cert is issued to www.bonicklandscaping.com
    def fake_headers(domain):
        return {"cert_cn": "www.bonicklandscaping.com", "location": None, "status": 200}

    monkeypatch.setattr("services.sdr_verification_gate._probe_headers", fake_headers)
    # No canonical tag on the resolved page -> Check 1's canonical step is a
    # no-op here. Mocked so this test stays offline (resolve_primary_site now
    # also fetches HTML for canonical corroboration -- FIX 1).
    monkeypatch.setattr("services.sdr_verification_gate._fetch_html", lambda d: "<html></html>")
    real, log = resolve_primary_site("bonick.com", "Bonick Landscaping")
    assert real == "www.bonicklandscaping.com"
    assert any("cert" in e.lower() for e in log)


def test_301_redirect_is_followed(monkeypatch):
    # Stride case: 301 to the real live site
    def fake_headers(domain):
        return {"cert_cn": domain, "location": "https://stridepestcontrol.com/", "status": 301}

    monkeypatch.setattr("services.sdr_verification_gate._probe_headers", fake_headers)
    monkeypatch.setattr("services.sdr_verification_gate._fetch_html", lambda d: "<html></html>")
    real, log = resolve_primary_site("stridepest.com", "Stride Pest")
    assert real == "stridepestcontrol.com"


def test_canonical_link_resolves_real_domain(monkeypatch):
    # No cert alias, no redirect -- Check 1's cert/redirect loop resolves to
    # the domain on file itself, but the page's own <link rel="canonical">
    # names a different real host, which Check 1 must adopt (spec section 5).
    monkeypatch.setattr(
        "services.sdr_verification_gate._probe_headers",
        lambda d: {"cert_cn": d, "location": None, "status": 200},
    )
    monkeypatch.setattr(
        "services.sdr_verification_gate._fetch_html",
        lambda d: '<html><head><link rel="canonical" href="https://realcanonical.com/"></head></html>',
    )
    real, log = resolve_primary_site("onfile.com", "Some Co")
    assert real == "realcanonical.com"
    assert any("canonical" in e.lower() for e in log)


def test_pinch_zoom_block_is_a_defect(monkeypatch):
    monkeypatch.setattr(
        "services.sdr_verification_gate._fetch_html",
        lambda d: '<meta name="viewport" content="width=device-width, maximum-scale=1.0">',
    )
    monkeypatch.setattr("services.sdr_verification_gate._ttfb", lambda d: 0.4)
    monkeypatch.setattr(
        "services.sdr_verification_gate._probe_headers",
        lambda d: {"status": 200, "cert_cn": d, "location": None},
    )
    d = check_defect("excaliburpest.com", "rebuild")
    assert d and d["kind"] == "pinch_zoom_blocked"
    assert "maximum-scale" in d["evidence"]


def test_healthy_site_has_no_defect(monkeypatch):
    monkeypatch.setattr(
        "services.sdr_verification_gate._fetch_html",
        lambda d: '<a href="tel:5551234567">Call</a><form></form>',
    )
    monkeypatch.setattr("services.sdr_verification_gate._ttfb", lambda d: 0.3)
    monkeypatch.setattr(
        "services.sdr_verification_gate._probe_headers",
        lambda d: {"status": 200, "cert_cn": d, "location": None},
    )
    assert check_defect("bonicklandscaping.com", "rebuild") is None


def test_phone_from_site_is_verified(monkeypatch):
    monkeypatch.setattr(
        "services.sdr_verification_gate._fetch_html",
        lambda d: 'Call us <a href="tel:+12813536366">(281) 353-6366</a>',
    )
    c = check_contact({"contact_name": "Craig"}, "excaliburpest.com")
    assert c and c["phone"] == "+12813536366" and c["source"] == "site"


def test_broker_only_contact_is_rejected(monkeypatch):
    monkeypatch.setattr("services.sdr_verification_gate._fetch_html", lambda d: "no phone here")
    # entity carries a broker-sourced phone; must NOT be accepted
    assert check_contact(
        {"contact_name": "X", "contact_phone": "+15550001111", "phone_source": "rocketreach"},
        "example.com",
    ) is None


def _req(**kw):
    base = dict(
        business_key="wd",
        entity={"domain_on_file": "x.com", "company_name": "X"},
        signal=None,
        motion="rebuild",
    )
    base.update(kw)
    return VerificationRequest(**base)


def test_all_pass_is_high_confidence_pass(monkeypatch):
    monkeypatch.setattr(
        "services.sdr_verification_gate.resolve_primary_site",
        lambda d, c, city="": ("x.com", ["same"]),
    )
    monkeypatch.setattr(
        "services.sdr_verification_gate.check_defect",
        lambda d, m: {"kind": "slow_load", "evidence": "TTFB 2.3s"},
    )
    monkeypatch.setattr(
        "services.sdr_verification_gate.check_contact",
        lambda e, d: {"name": "A", "phone": "+1555", "source": "site"},
    )
    monkeypatch.setattr(
        "services.sdr_verification_gate._primary_is_our_domain", lambda dof, real: True
    )
    r = verify(_req())
    assert r.verdict == "PASS" and r.confidence >= 0.75


def test_real_site_elsewhere_is_fail(monkeypatch):
    monkeypatch.setattr(
        "services.sdr_verification_gate.resolve_primary_site",
        lambda d, c, city="": ("realsite.com", ["alias"]),
    )
    monkeypatch.setattr(
        "services.sdr_verification_gate._primary_is_our_domain", lambda dof, real: False
    )
    monkeypatch.setattr("services.sdr_verification_gate.check_defect", lambda d, m: None)
    r = verify(_req())
    assert r.verdict == "FAIL"


def test_pass_auto_approves_and_pushes(monkeypatch):
    monkeypatch.setattr(
        "services.sdr_verification_gate.verify",
        lambda r: VerificationResult(
            "PASS", "x.com", {"kind": "slow_load", "evidence": "e"},
            {"name": "A", "phone": "+1555", "source": "site"}, 0.85, ["log"], "ok",
        ),
    )
    calls = {}
    monkeypatch.setattr(
        "services.sdr_verification_gate._queue",
        lambda **k: (calls.setdefault("queue", k), "auto_approved")[1],
    )
    monkeypatch.setattr(
        "services.sdr_verification_gate._push_crm",
        lambda **k: calls.setdefault("push", k) or ("twenty", [{"status": "created"}]),
    )
    out = run(VerificationRequest("wd", {"domain_on_file": "x.com", "company_name": "X"}, None, "rebuild"))
    assert out["queue_status"] == "auto_approved"
    assert "push" in calls and calls["push"]["business_key"] == "wd"


def test_fail_never_queues_or_pushes(monkeypatch):
    monkeypatch.setattr(
        "services.sdr_verification_gate.verify",
        lambda r: VerificationResult("FAIL", "real.com", None, None, 0.0, ["log"], "real site elsewhere"),
    )
    calls = {}
    monkeypatch.setattr("services.sdr_verification_gate._queue", lambda **k: calls.setdefault("queue", k))
    monkeypatch.setattr("services.sdr_verification_gate._push_crm", lambda **k: calls.setdefault("push", k))
    out = run(VerificationRequest("wd", {"domain_on_file": "x.com", "company_name": "X"}, None, "rebuild"))
    assert out["verdict"] == "FAIL" and "queue" not in calls and "push" not in calls
