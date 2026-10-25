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
