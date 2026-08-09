"""Book'd handoff staging: name validation, encrypt-at-rest, one-shot reveal."""
from unittest import mock

from services import bookd_handoff as BH


def test_stage_rejects_bad_key_names_and_empty_values(monkeypatch):
    monkeypatch.setattr(BH, "_alert", lambda s, t: True)
    assert BH.stage("not a name", "value123")["ok"] is False
    assert BH.stage("STRIPE_WEBHOOK_SECRET", "")["ok"] is False


def test_stage_encrypts_and_never_alerts_the_value(monkeypatch, ):
    monkeypatch.setenv("APP_SECRET", "test-secret-for-fernet-derivation")
    writes, alerts = [], []
    monkeypatch.setattr(BH, "execute_query", lambda sql, params=None: writes.append((sql, params)))
    monkeypatch.setattr(BH, "fetch_all", lambda sql, params=None:
                        writes.append((sql, params)) or [(42,)])
    monkeypatch.setattr(BH, "_alert", lambda subj, text: alerts.append((subj, text)) or True)
    out = BH.stage("STRIPE_WEBHOOK_SECRET", "whsec_supersecret999", "hermes-mcp")
    assert out == {"ok": True, "id": 42, "key_name": "STRIPE_WEBHOOK_SECRET", "status": "staged"}
    blob = " ".join(str(w) for w in writes)
    assert "whsec_supersecret999" not in blob            # only ciphertext touches the DB
    assert alerts and "whsec_supersecret999" not in alerts[0][0] + alerts[0][1]
    assert "STRIPE_WEBHOOK_SECRET" in alerts[0][0]       # Michael sees the NAME only


def test_reveal_is_one_shot(monkeypatch):
    monkeypatch.setenv("APP_SECRET", "test-secret-for-fernet-derivation")
    enc = BH._fernet().encrypt(b"whsec_supersecret999").decode()
    state = {"status": "staged"}
    monkeypatch.setattr(BH, "fetch_all", lambda sql, params=None:
                        [("STRIPE_WEBHOOK_SECRET", enc, state["status"])])
    monkeypatch.setattr(BH, "execute_query", lambda sql, params=None:
                        state.update(status="revealed") if "UPDATE" in sql else None)
    first = BH.reveal(7)
    assert first["ok"] is True and first["value"] == "whsec_supersecret999"
    second = BH.reveal(7)
    assert second["ok"] is False and "one-shot" in second["error"]
