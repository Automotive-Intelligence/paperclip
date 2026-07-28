"""tests/test_sdr_engine.py -- unit tests for the SDR Engine
(services/sdr_engine.py). Network + CRM + GitHub seams are monkeypatched at
the module boundary throughout, per the plan
(docs/superpowers/plans/2026-07-27-sdr-engine-shadow.md) -- nothing here
touches the wire.
"""

import pytest

from services.sdr_engine import crm_ready, resolve_business_key


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
