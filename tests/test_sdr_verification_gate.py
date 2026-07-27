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


def test_slow_ttfb_is_a_defect(monkeypatch):
    # Healthy headers (no cert error, no down status) and a homepage with a
    # real CTA (so it clears no_contact_path) -- only TTFB trips the defect.
    monkeypatch.setattr(
        "services.sdr_verification_gate._probe_headers",
        lambda d: {"status": 200, "cert_cn": d, "location": None},
    )
    monkeypatch.setattr(
        "services.sdr_verification_gate._fetch_html",
        lambda d: "<html><body>Contact us for a free quote.</body></html>",
    )
    monkeypatch.setattr("services.sdr_verification_gate._ttfb", lambda d: 2.3)
    d = check_defect("poolologypools.com", "rebuild")
    assert d and d["kind"] == "slow_load"
    assert "2.3" in d["evidence"]


def test_cert_warning_is_a_defect(monkeypatch):
    # A TLS cert failure aborts curl before any HTTP status is captured
    # (status ends up None, same as a down site) -- cert_error must be
    # checked BEFORE the generic down-status check so this gets the more
    # specific, more actionable "cert_warning" diagnosis, not "site_down".
    monkeypatch.setattr(
        "services.sdr_verification_gate._probe_headers",
        lambda d: {
            "status": None,
            "cert_cn": None,
            "location": None,
            "cert_error": True,
            "cert_error_detail": "curl: (60) SSL certificate problem: unable to get local issuer certificate",
        },
    )
    d = check_defect("badcert.example.com", "rebuild")
    assert d and d["kind"] == "cert_warning"
    assert "certificate" in d["evidence"].lower()


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
        lambda d: (
            'Call us <a href="tel:+12813536366">(281) 353-6366</a> or '
            'email <a href="mailto:info@excaliburpest.com">info@excaliburpest.com</a>'
        ),
    )
    c = check_contact({"contact_name": "Craig"}, "excaliburpest.com")
    assert c and c["phone"] == "+12813536366" and c["source"] == "site"
    assert c["email"] == "info@excaliburpest.com"


def test_email_only_contact_is_verified_via_mailto(monkeypatch):
    # No tel: link and no trusted-source entity phone -- but the site's own
    # homepage publishes a mailto:, which is itself a verified contact
    # channel per spec section 5 ("phone/email"), never a data broker.
    monkeypatch.setattr(
        "services.sdr_verification_gate._fetch_html",
        lambda d: '<a href="mailto:hello@poolologypools.com">Email us</a>',
    )
    c = check_contact({"contact_name": "Front Desk"}, "poolologypools.com")
    assert c and c["email"] == "hello@poolologypools.com"
    assert c["phone"] is None
    assert c["source"] == "site"


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


def test_intent_motion_routes_to_human_not_fabricated_pass(monkeypatch):
    # check_defect now honestly returns None for a non-rebuild motion
    # (re-confirmation isn't wired) -- verify() must route that to
    # NEEDS_HUMAN, never auto-PASS on an unconfirmed signal, and must never
    # carry a fabricated "re-confirmed" evidence string.
    monkeypatch.setattr(
        "services.sdr_verification_gate.resolve_primary_site",
        lambda d, c, city="": ("x.com", ["same"]),
    )
    monkeypatch.setattr(
        "services.sdr_verification_gate.check_contact",
        lambda e, d: {"name": "A", "phone": "+1555", "source": "site"},
    )
    r = verify(_req(motion="intent"))
    assert r.verdict == "NEEDS_HUMAN"
    assert r.verified_defect is None
    assert "not yet wired" in r.reason.lower()
    assert not any("re-confirmed" in e.lower() for e in r.evidence_log)


def test_intent_motion_can_fail_when_signal_confirmed_stale(monkeypatch):
    # Structural requirement: intent/permit motions must be able to reach
    # FAIL, not just NEEDS_HUMAN/PASS -- once re-confirmation IS wired and
    # comes back with a definitive "signal no longer live" result, verify()
    # must be able to FAIL it, not just hand it to a human every time.
    monkeypatch.setattr(
        "services.sdr_verification_gate.resolve_primary_site",
        lambda d, c, city="": ("x.com", ["same"]),
    )
    monkeypatch.setattr(
        "services.sdr_verification_gate.check_defect",
        lambda d, m: {"kind": "signal_stale", "evidence": "signal expired 40 days ago"},
    )
    r = verify(_req(motion="intent"))
    assert r.verdict == "FAIL"


def test_llm_offcontract_verdict_fails_closed_to_human(monkeypatch):
    # Even an explicit "PASS" from the LLM only clears the primary-site
    # ambiguity -- it can never by itself auto-approve the whole prospect;
    # Check 3 (contact) still independently gates the final verdict. No
    # trusted-source contact here, so the final verdict must still be
    # NEEDS_HUMAN, not PASS.
    monkeypatch.setattr(
        "services.sdr_verification_gate.resolve_primary_site",
        lambda d, c, city="": ("x.com", ["same"]),
    )
    monkeypatch.setattr(
        "services.sdr_verification_gate._primary_is_our_domain", lambda dof, real: True
    )
    monkeypatch.setattr(
        "services.sdr_verification_gate.check_defect",
        lambda d, m: {"kind": "site_down", "evidence": "HTTP 404 on https://x.com"},
    )
    monkeypatch.setattr(
        "services.studio_social_llm.llm_json",
        lambda *a, **kw: {"verdict": "PASS", "rationale": "x"},
    )
    monkeypatch.setattr("services.sdr_verification_gate.check_contact", lambda e, d: None)
    r = verify(_req())
    assert r.verdict == "NEEDS_HUMAN"


def test_llm_missing_verdict_key_fails_closed_to_human(monkeypatch):
    # The actual fail-closed mechanism (FIX A): a well-formed-but-off-contract
    # LLM answer -- here, no "verdict" key at all -- must NOT auto-approve.
    # Contact IS trusted/verified here specifically to ISOLATE this from the
    # contact-unverified branch: under the pre-fix code (which only escalated
    # on an exact "NEEDS_HUMAN" string) this exact scenario incorrectly fell
    # through all the way to PASS. With a trusted contact present, the only
    # thing that can still produce NEEDS_HUMAN is the LLM-verdict handling
    # itself.
    monkeypatch.setattr(
        "services.sdr_verification_gate.resolve_primary_site",
        lambda d, c, city="": ("x.com", ["same"]),
    )
    monkeypatch.setattr(
        "services.sdr_verification_gate._primary_is_our_domain", lambda dof, real: True
    )
    monkeypatch.setattr(
        "services.sdr_verification_gate.check_defect",
        lambda d, m: {"kind": "site_down", "evidence": "HTTP 404 on https://x.com"},
    )
    monkeypatch.setattr(
        "services.studio_social_llm.llm_json",
        lambda *a, **kw: {"rationale": "no verdict field at all"},
    )
    monkeypatch.setattr(
        "services.sdr_verification_gate.check_contact",
        lambda e, d: {"name": "A", "phone": "+1555", "source": "site"},
    )
    r = verify(_req())
    assert r.verdict == "NEEDS_HUMAN"


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
