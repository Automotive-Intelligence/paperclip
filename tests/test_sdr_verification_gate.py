"""tests/test_sdr_verification_gate.py -- unit tests for the SDR Verification
Gate (services/sdr_verification_gate.py). Each check is exercised in
isolation via monkeypatch on the module's network seams (_probe_headers,
_fetch_html, _ttfb) so nothing touches the wire. See
docs/superpowers/plans/2026-07-27-sdr-verification-gate.md for the plan this
implements task-by-task, and
docs/superpowers/specs/2026-07-27-sdr-verification-gate-design.md for the
contract.
"""

from services.sdr_verification_gate import check_defect, resolve_primary_site


def test_cert_cn_mismatch_resolves_to_real_host(monkeypatch):
    # Bonick case: domain_on_file cert is issued to www.bonicklandscaping.com
    def fake_headers(domain):
        return {"cert_cn": "www.bonicklandscaping.com", "location": None, "status": 200}

    monkeypatch.setattr("services.sdr_verification_gate._probe_headers", fake_headers)
    real, log = resolve_primary_site("bonick.com", "Bonick Landscaping")
    assert real == "www.bonicklandscaping.com"
    assert any("cert" in e.lower() for e in log)


def test_301_redirect_is_followed(monkeypatch):
    # Stride case: 301 to the real live site
    def fake_headers(domain):
        return {"cert_cn": domain, "location": "https://stridepestcontrol.com/", "status": 301}

    monkeypatch.setattr("services.sdr_verification_gate._probe_headers", fake_headers)
    real, log = resolve_primary_site("stridepest.com", "Stride Pest")
    assert real == "stridepestcontrol.com"


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
