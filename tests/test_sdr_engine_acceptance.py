"""tests/test_sdr_engine_acceptance.py -- the ship gate for the SDR Engine
(sub-project #2 of the autonomous SDR desk). A full shadow run over a mixed
fixture batch of unverified Twenty candidates must produce a correct digest
and make ZERO live writes: no CRM push, no receipt publish (and, per
services/sdr_engine.py's `_gate_run` Task 0 pin, no approval_queue write
either -- shadow calls the gate's pure `verify()`, never its
write-performing `run()`). See docs/superpowers/plans/
2026-07-27-sdr-engine-shadow.md Task 6.
"""

from services.sdr_engine import run_sdr_engine


def test_shadow_run_over_mixed_batch_makes_zero_live_writes(monkeypatch):
    candidates = [
        {"twenty_id": "1", "company_name": "Spike Fence", "domain_on_file": "spikefence.com",
         "contact_name": "Pat", "contact_phone": "+15551230000", "contact_email": None},
        {"twenty_id": "2", "company_name": "Bonick Landscaping", "domain_on_file": "bonick.com",
         "contact_name": None, "contact_phone": None, "contact_email": None},
        {"twenty_id": "3", "company_name": "Taps Plumbing", "domain_on_file": "tapsplumbing.com",
         "contact_name": None, "contact_phone": None, "contact_email": None},
    ]
    monkeypatch.setattr(
        "services.sdr_engine.read_unverified_candidates",
        lambda rk, limit=100: candidates,
    )

    # Real-ish verdicts, one of each bucket -- mirrors the gate's own
    # documented outcomes (spec section 9 / the gate's golden fixtures):
    # a clean rebuild PASS, a "real site elsewhere" FAIL, and an
    # unverified-contact NEEDS_HUMAN.
    verdicts = {
        "Spike Fence": {"verdict": "PASS", "queue_status": "auto_approved", "crm": None,
                        "reason": "all checks passed"},
        "Bonick Landscaping": {"verdict": "FAIL", "queue_status": None, "crm": None,
                               "reason": "real primary site is www.bonicklandscaping.com, "
                                         "not bonick.com; not a rebuild target"},
        "Taps Plumbing": {"verdict": "NEEDS_HUMAN", "queue_status": "pending_approval", "crm": None,
                          "reason": "contact unverified (no published number)"},
    }

    def fake_gate_run(*, request, commit=False):
        assert commit is False  # a shadow engine run must never ask the gate to commit
        return verdicts[request.entity["company_name"]]

    monkeypatch.setattr("services.sdr_engine._gate_run", fake_gate_run)

    pushed = []
    monkeypatch.setattr("services.sdr_engine._push_crm", lambda **k: pushed.append(k))
    published = []
    monkeypatch.setattr("services.sdr_engine._commit_receipt", lambda path, body: published.append(path))

    out = run_sdr_engine("wd", commit=False)

    # The ship gate: zero live writes of any kind.
    assert pushed == []
    assert published == []

    # Correct, honest counts -- every real gate verdict accounted for.
    assert out["produced"] == 3
    assert out["pass"] == 1
    assert out["fail"] == 1
    assert out["needs_human"] == 1
    assert out["written"] == 0

    # The digest is always produced (both modes) and reports only real gate
    # output -- every candidate + its real verdict + real reason is present.
    digest = out["digest"]
    for name, v in verdicts.items():
        assert name in digest
        assert v["verdict"] in digest
        assert v["reason"] in digest
    assert out["digest_path"]

    # No em-dashes in emitted digest copy (binding global constraint).
    assert "—" not in digest
