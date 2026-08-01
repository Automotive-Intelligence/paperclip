"""lead_store: durable-first, idempotent, always-alert, fail-closed (funnel items 5,6)."""
from unittest import mock

from services import lead_store as LS


def _patch(stack, *, prior=None, ghl=True, email=True, issue=False):
    p = stack.enter_context
    calls = []
    p(mock.patch.object(LS, "execute_query", side_effect=lambda *a, **k: calls.append(a[0][:24])))
    p(mock.patch.object(LS, "fetch_all", return_value=(prior if prior is not None else [(False, None)])))
    p(mock.patch.object(LS, "_ghl_write", return_value=ghl))
    p(mock.patch.object(LS, "_alert_email", return_value=email))
    p(mock.patch.object(LS, "_alert_issue", return_value=issue))
    return calls


def test_real_lead_stored_before_alert_and_ok_true():
    from contextlib import ExitStack
    with ExitStack() as s:
        calls = _patch(s)
        r = LS.ingest_lead({"brand": "aipg", "name": "Jane Doe", "phone": "555", "email": "j@x.co"})
        assert any(c.startswith("INSERT INTO leads") for c in calls)  # durable store happened
        assert LS._alert_email.called
    assert r["ok"] is True and r["stored"] is True
    assert r["via"] == "email" and r["ghl_ok"] is True and r["status"] == "delivered"


def test_ok_false_when_durable_store_fails():
    with mock.patch.object(LS, "execute_query", side_effect=RuntimeError("db down")):
        r = LS.ingest_lead({"brand": "aipg", "name": "x"})
    assert r["ok"] is False and r["stored"] is False


def test_fail_closed_when_no_human_alerted():
    from contextlib import ExitStack
    with ExitStack() as s:
        _patch(s, ghl=False, email=False, issue=False)
        r = LS.ingest_lead({"brand": "aipg", "name": "x", "phone": "1"})
    assert r["ok"] is False                     # nobody told -> fail closed
    assert r["status"] == "dead_letter"         # GHL failed too -> dead letter, still alerts attempted


def test_dedup_on_replay_does_not_realert():
    from contextlib import ExitStack
    with ExitStack() as s:
        _patch(s, prior=[(True, "email")])      # already alerted on a prior call
        r = LS.ingest_lead({"brand": "aipg", "name": "x", "phone": "1"})
        assert not LS._alert_email.called       # no second alert on replay
    assert r["deduped"] is True and r["ok"] is True


def test_synthetic_stores_but_skips_ghl_and_human_alert():
    from contextlib import ExitStack
    with ExitStack() as s:
        _patch(s)
        r = LS.ingest_lead({"brand": "aipg", "name": "CANARY", "synthetic": True})
        assert not LS._ghl_write.called and not LS._alert_email.called
    assert r["ok"] is True and r["via"] == "synthetic" and r["status"] == "verified"


def test_idempotency_key_stable_and_explicit_wins():
    a = LS._idempotency_key({"brand": "aipg", "email": "J@x.co", "phone": "555", "name": "Jane"})
    b = LS._idempotency_key({"brand": "aipg", "email": "j@x.co", "phone": "555", "name": "jane"})
    assert a == b                               # case/whitespace-insensitive, same hour
    assert LS._idempotency_key({"idempotency_key": "explicit-123"}) == "explicit-123"


def test_ghl_write_retries_5xx_not_4xx():
    class _R:
        def __init__(self, code): self.status_code, self.ok = code, code < 300
        def text(self): return "err"
        text = property(lambda self: "err")
    with mock.patch.dict("os.environ", {"GHL_API_KEY": "k", "GHL_LOCATION_ID": "l"}):
        # 500 then 200 -> retried to success
        with mock.patch.object(LS.requests, "post", side_effect=[_R(500), _R(200)]):
            assert LS._ghl_write({"name": "A B", "email": "a@b.co"}) is True
        # 400 -> not retried, one call
        post = mock.Mock(return_value=_R(400))
        with mock.patch.object(LS.requests, "post", post):
            assert LS._ghl_write({"name": "A"}) is False
            assert post.call_count == 1
