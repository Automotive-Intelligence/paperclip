"""tests/test_sdr_verification_gate_acceptance.py -- the ship gate.

Golden acceptance test: the SDR Verification Gate must reproduce last
week's correct human judgment on all seven 2026-07-15 WD rebuild leads
(spec section 9). Fixtures are built from the DOCUMENTED evidence in the
spec, not re-curled from the live sites (their state may have drifted since
2026-07-15) -- see tests/fixtures/verification/rebuilds/*.json, each with a
"_note" explaining exactly which spec fact it encodes and which check path
it is meant to exercise. This makes the test a deterministic regression of
the correct human judgment, fully offline.

If a case here mismatches, fix the CHECK LOGIC, not the expected verdict --
per the implementation plan, this is the ship gate.
"""

import json

import pytest

from services.sdr_verification_gate import VerificationRequest, verify

EXPECTED = {
    "spike": "PASS",
    "excalibur": "PASS",
    "poolology": "PASS",
    "bonick": "FAIL",
    "stride": "FAIL",
    "taps": "NEEDS_HUMAN",
    "poolpros": "NEEDS_HUMAN",
}


@pytest.mark.parametrize("name,expected", EXPECTED.items())
def test_rebuild_verdict_matches_human(name, expected, monkeypatch):
    with open(f"tests/fixtures/verification/rebuilds/{name}.json") as f:
        fx = json.load(f)

    monkeypatch.setattr("services.sdr_verification_gate._probe_headers", lambda d: fx["headers"])
    monkeypatch.setattr("services.sdr_verification_gate._fetch_html", lambda d: fx["html"])
    monkeypatch.setattr("services.sdr_verification_gate._ttfb", lambda d: fx["ttfb"])
    # services.studio_social_llm.llm_json is the real two-positional-arg
    # seam (system, user) -- verify() imports it locally at call time, so
    # patching the attribute on its home module (not on
    # sdr_verification_gate's namespace) is what actually takes effect.
    monkeypatch.setattr(
        "services.studio_social_llm.llm_json",
        lambda *a, **kw: fx.get("llm", {"verdict": "NEEDS_HUMAN", "rationale": "ambiguous"}),
    )

    r = verify(VerificationRequest("wd", fx["entity"], None, "rebuild"))
    assert r.verdict == expected, f"{name}: got {r.verdict} ({r.reason})"
