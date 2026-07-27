"""tests/test_sdr_verification_gate.py -- unit tests for the SDR Verification
Gate (services/sdr_verification_gate.py). Each check is exercised in
isolation via monkeypatch on the module's network seams (_probe_headers,
_fetch_html, _ttfb) so nothing touches the wire. See
docs/superpowers/plans/2026-07-27-sdr-verification-gate.md for the plan this
implements task-by-task, and
docs/superpowers/specs/2026-07-27-sdr-verification-gate-design.md for the
contract.
"""

from services.sdr_verification_gate import resolve_primary_site


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
