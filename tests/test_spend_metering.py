"""Spend-metering truth (2026-08-30): the live call sites record to the ledger,
provider ground truth is snapshotted, and the email fails CLOSED on zero rows."""
from datetime import datetime, timezone
from unittest import mock

from services import llm_ledger as L
from services import spend_email as E
from services import slipstream_generate as sg
from services import studio_social_llm as SS


# ---- ledger: OpenRouter recorder ------------------------------------------

def test_openrouter_response_uses_provider_cost_when_present():
    j = {"id": "gen-1", "model": "google/gemini-2.5-pro",
         "usage": {"prompt_tokens": 1200, "completion_tokens": 3400, "cost": 0.0123}}
    with mock.patch.object(L, "record_usage", return_value=0.0123) as rec:
        L.record_openrouter_response(j, model="x", persona="slipstream", surface="slipstream")
    kw = rec.call_args.kwargs
    assert rec.call_args.args[0] == "google/gemini-2.5-pro"
    assert kw["input_tokens"] == 1200 and kw["output_tokens"] == 3400
    assert kw["cost_usd_override"] == 0.0123 and kw["persona"] == "slipstream"


def test_openrouter_response_falls_back_to_token_pricing():
    j = {"usage": {"prompt_tokens": 10, "completion_tokens": 20}}
    with mock.patch.object(L, "record_usage") as rec:
        L.record_openrouter_response(j, model="fallback-model")
    assert rec.call_args.args[0] == "fallback-model"
    assert rec.call_args.kwargs["cost_usd_override"] is None


def test_openrouter_response_never_raises():
    assert L.record_openrouter_response(None, model="m") is None


# ---- ledger: provider snapshots -------------------------------------------

def test_provider_delta_none_without_baseline():
    with mock.patch.object(L, "fetch_all", return_value=[]):
        assert L.provider_delta("openrouter", 100.0) is None


def test_provider_delta_is_spend_since_prior_snapshot():
    ts = datetime(2026, 8, 29, 12, 55, tzinfo=timezone.utc)
    with mock.patch.object(L, "fetch_all", return_value=[(130.19, ts)]):
        d = L.provider_delta("openrouter", 131.49)
    assert abs(d["spent_usd"] - 1.30) < 1e-6 and d["since"] == ts


def test_provider_delta_never_negative():
    ts = datetime(2026, 8, 29, tzinfo=timezone.utc)
    with mock.patch.object(L, "fetch_all", return_value=[(200.0, ts)]):
        assert L.provider_delta("openrouter", 150.0)["spent_usd"] == 0.0


# ---- call sites actually record ------------------------------------------

class _Resp:
    ok = True
    status_code = 200
    text = ""

    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def test_slipstream_call_site_records_and_requests_provider_cost(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    payload = {"choices": [{"message": {"content": '{"ok": true}'}}],
               "usage": {"prompt_tokens": 5, "completion_tokens": 7, "cost": 0.001}}
    with mock.patch.object(sg.requests, "post", return_value=_Resp(payload)) as post, \
         mock.patch("services.llm_ledger.record_openrouter_response") as rec:
        out = sg._llm_json_once("sys", "user")
    assert out == {"ok": True}
    assert post.call_args.kwargs["json"]["usage"] == {"include": True}
    rec.assert_called_once()
    assert rec.call_args.kwargs["persona"] == "slipstream"


def test_slipstream_ledger_failure_never_breaks_a_post(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    payload = {"choices": [{"message": {"content": '{"ok": true}'}}]}
    with mock.patch.object(sg.requests, "post", return_value=_Resp(payload)), \
         mock.patch("services.llm_ledger.record_openrouter_response", side_effect=RuntimeError("db")):
        assert sg._llm_json_once("sys", "user") == {"ok": True}


def test_studio_call_site_records_anthropic_usage(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    payload = {"id": "msg_1", "model": "claude-sonnet-5",
               "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 10}}
    with mock.patch.object(SS.requests, "post", return_value=_Resp(payload)), \
         mock.patch("services.llm_ledger.record_usage") as rec:
        out = SS._post_messages({"model": "claude-sonnet-5"})
    assert out == payload
    assert rec.call_args.args[0] == "claude-sonnet-5"
    kw = rec.call_args.kwargs
    assert kw["input_tokens"] == 100 and kw["output_tokens"] == 50 and kw["cache_read_tokens"] == 10
    assert kw["persona"] == "studio-social"


# ---- email: fail closed + provider truth ----------------------------------

def _totals(calls, orx=None):
    return {"day": "2026-08-29", "total_usd": 0.0 if not calls else 1.5, "calls": calls,
            "by_persona": [], "by_model": [], "by_client": [], "openrouter": orx}


def test_zero_rows_renders_metering_gap_not_quiet_day():
    html = E._build_html(_totals(0))
    assert "metering gap" in html and "Ledger recorded 0 calls" in html


def test_nonzero_rows_have_no_gap_banner():
    assert "metering gap" not in E._build_html(_totals(3))


def test_provider_section_renders_delta_and_balance():
    orx = {"total_usage": 131.49, "balance": 18.51,
           "delta": {"spent_usd": 1.3, "since": datetime(2026, 8, 29, 12, 55, tzinfo=timezone.utc)}}
    html = E._build_html(_totals(0, orx))
    assert "$1.30 since 2026-08-29 12:55 UTC" in html and "$18.51" in html


def test_subject_never_reads_zero_dollars_on_empty_ledger():
    orx = {"total_usage": 1, "balance": 1, "delta": {"spent_usd": 1.3, "since": datetime(2026, 8, 29)}}
    subj = E._subject(_totals(0, orx))
    assert "$0.00" not in subj and "metering gap" in subj and "OpenRouter $1.30" in subj


def test_subject_normal_when_ledger_has_rows():
    assert E._subject(_totals(4)).startswith("💸 AI spend 2026-08-29: $1.50 (4 calls)")
