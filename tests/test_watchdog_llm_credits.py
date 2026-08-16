"""LLM fuel gauge: balance thresholds, dead-key fail-closed, network skip."""
import os
from unittest import mock

from services import watchdog


class _Resp:
    def __init__(self, code=200, payload=None):
        self.status_code = code
        self.ok = 200 <= code < 300
        self._payload = payload or {}

    def json(self):
        return self._payload


def _cfg(warn=10, critical=1):
    return {"llm_credits": {"enabled": True, "warn_floor": warn, "critical_floor": critical}}


def _credits(total, usage):
    return {"data": {"total_credits": total, "total_usage": usage}}


def _key():
    return mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-x"})


def test_disabled_probes_nothing():
    with mock.patch.object(watchdog.requests, "get") as seam:
        assert watchdog._check_llm_credits({"llm_credits": {"enabled": False}}) == []
        seam.assert_not_called()


def test_healthy_balance_is_quiet():
    with _key(), mock.patch.object(watchdog.requests, "get",
                                   return_value=_Resp(200, _credits(130, 50))):
        assert watchdog._check_llm_credits(_cfg()) == []


def test_low_balance_warns():
    with _key(), mock.patch.object(watchdog.requests, "get",
                                   return_value=_Resp(200, _credits(130, 125))):
        out = watchdog._check_llm_credits(_cfg())
    assert [a.fingerprint for a in out] == ["llm-credits-low"]
    assert out[0].severity == "warn"


def test_exhausted_balance_is_critical():
    """The 2026-08-14 blackout state: $130.00 bought, $130.19 used."""
    with _key(), mock.patch.object(watchdog.requests, "get",
                                   return_value=_Resp(200, _credits(130.00, 130.19))):
        out = watchdog._check_llm_credits(_cfg())
    assert [a.fingerprint for a in out] == ["llm-credits-exhausted"]
    assert out[0].severity == "critical"


def test_dead_key_is_critical_not_skip():
    with _key(), mock.patch.object(watchdog.requests, "get", return_value=_Resp(401)):
        out = watchdog._check_llm_credits(_cfg())
    assert [a.fingerprint for a in out] == ["llm-credits-unwatchable"]
    assert out[0].severity == "critical"


def test_missing_key_is_critical():
    with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
        out = watchdog._check_llm_credits(_cfg())
    assert [a.fingerprint for a in out] == ["llm-credits-unwatchable"]


def test_network_error_is_skip():
    with _key(), mock.patch.object(watchdog.requests, "get",
                                   side_effect=watchdog.requests.ConnectionError("x")):
        assert watchdog._check_llm_credits(_cfg()) == []


def test_registered_with_runbook():
    assert watchdog._check_llm_credits in watchdog._CHECKS
    assert watchdog._runbook("llm-credits-exhausted")
    assert watchdog._runbook("llm-credits-low")
    assert watchdog._runbook("llm-credits-unwatchable")
