"""Test-wide guards."""
import pytest


@pytest.fixture(autouse=True)
def _no_network_live_wait(monkeypatch):
    """slipstream_engine._distribute_social waits for the live site before
    scheduling social. Tests use fake URLs, so without this every test that
    reaches the distributor would poll the real network for ten minutes.
    Tests that exercise the wait itself pass their own `check`/`sleep`."""
    try:
        from services import slipstream_engine as se
    except Exception:
        return
    monkeypatch.setattr(se, "_url_live", lambda url: True)


@pytest.fixture(autouse=True)
def _offline_factcheck_gate(monkeypatch):
    """run_brand now runs the fact-check + editor gate, which fetches every link
    and calls a second model. Unit tests must do neither, so by default the gate
    is a stub that passes. Tests of the gate itself call
    services.slipstream_factcheck directly with fake HTTP and a fake LLM, and
    tests of the engine wiring re-patch se.run_factcheck with their own result.
    This stub exists ONLY in tests: production has no bypass switch."""
    try:
        from services import slipstream_engine as se
    except Exception:
        return
    monkeypatch.setattr(se, "run_factcheck", lambda post, cfg, **kw: {
        "ok": True, "violations": [], "error": None, "claims": [], "editor": [],
        "links": [], "counts": {}, "model": "test-stub", "llm_ran": False})
