"""
tests/test_phase4_activation.py — Phase 4: Activation Layer tests

Tests cover:
  1. Artifact creation — validation, risk derivation, moral gate, status routing
  2. Approval queue — persist, get_pending, approve/reject transitions
  3. Dispatch — auto-dispatch on auto_approved, error on wrong status
  4. Delivery receipt — make_receipt factory
  5. API endpoints — POST /api/artifacts, GET /api/artifacts/pending, approve/reject
"""

import os
import json
import datetime
import unittest
from dataclasses import asdict
from unittest.mock import patch, MagicMock, call


# ---------------------------------------------------------------------------
# 1. Artifact creation tests — pure logic, no DB needed
# ---------------------------------------------------------------------------

class ArtifactCreationTests(unittest.TestCase):

    def test_valid_artifact_created(self):
        from services.artifact import create_artifact
        a = create_artifact(
            agent_id="tyler",
            business_key="aiphoneguy",
            artifact_type="email",
            audience="prospect",
            intent="nurture",
            content="Hi there, just checking in.",
            subject="Quick note",
            confidence=0.9,
        )
        self.assertEqual(a.agent_id, "tyler")
        self.assertEqual(a.artifact_type, "email")
        self.assertIn(a.status, ("auto_approved", "pending_approval", "escalated"))
        self.assertIsNotNone(a.artifact_id)

    def test_invalid_artifact_type_raises(self):
        from services.artifact import create_artifact
        with self.assertRaises(ValueError):
            create_artifact(
                agent_id="tyler",
                business_key="aiphoneguy",
                artifact_type="tweet",   # not a valid type
                audience="prospect",
                intent="nurture",
                content="hello",
            )

    def test_invalid_audience_raises(self):
        from services.artifact import create_artifact
        with self.assertRaises(ValueError):
            create_artifact(
                agent_id="tyler",
                business_key="aiphoneguy",
                artifact_type="email",
                audience="everyone",   # not a valid audience
                intent="nurture",
                content="hello",
            )

    def test_invalid_intent_raises(self):
        from services.artifact import create_artifact
        with self.assertRaises(ValueError):
            create_artifact(
                agent_id="tyler",
                business_key="aiphoneguy",
                artifact_type="email",
                audience="prospect",
                intent="spam",    # not a valid intent
                content="hello",
            )

    def test_confidence_clamped(self):
        from services.artifact import create_artifact
        a = create_artifact(
            agent_id="tyler",
            business_key="aiphoneguy",
            artifact_type="note",
            audience="internal",
            intent="inform",
            content="Agent note.",
            confidence=5.0,   # over 1.0 — should be clamped to 1.0
        )
        self.assertEqual(a.confidence, 1.0)

    def test_low_risk_high_confidence_auto_approved(self):
        from services.artifact import create_artifact
        a = create_artifact(
            agent_id="atlas",
            business_key="autointelligence",
            artifact_type="note",
            audience="internal",
            intent="inform",
            content="Daily briefing note.",
            confidence=0.95,
        )
        self.assertEqual(a.risk_level, "low")
        self.assertFalse(a.requires_human_approval)
        self.assertEqual(a.status, "auto_approved")

    def test_high_risk_ad_artifact_escalated(self):
        from services.artifact import create_artifact
        a = create_artifact(
            agent_id="michael_meta",
            business_key="autointelligence",
            artifact_type="ad",
            audience="public",
            intent="close",
            content="Buy now!",
            confidence=0.9,
        )
        self.assertEqual(a.risk_level, "high")
        self.assertTrue(a.requires_human_approval)
        self.assertEqual(a.status, "escalated")

    def test_prospect_email_nurture_medium_risk(self):
        from services.artifact import create_artifact
        a = create_artifact(
            agent_id="tyler",
            business_key="aiphoneguy",
            artifact_type="email",
            audience="prospect",
            intent="nurture",
            content="Just reaching out...",
            confidence=0.85,
        )
        self.assertEqual(a.risk_level, "medium")
        self.assertTrue(a.requires_human_approval)
        self.assertEqual(a.status, "pending_approval")

    def test_default_channel_candidates_set(self):
        from services.artifact import create_artifact
        a = create_artifact(
            agent_id="tyler",
            business_key="aiphoneguy",
            artifact_type="email",
            audience="prospect",
            intent="nurture",
            content="Hello.",
        )
        self.assertEqual(a.channel_candidates, ["email"])

    def test_to_dict_serializable(self):
        from services.artifact import create_artifact
        import json
        a = create_artifact(
            agent_id="tyler",
            business_key="aiphoneguy",
            artifact_type="note",
            audience="internal",
            intent="inform",
            content="Note.",
        )
        d = a.to_dict()
        # Must be JSON-serializable
        serialized = json.dumps(d)
        self.assertIn(a.artifact_id, serialized)


# ---------------------------------------------------------------------------
# 2. Delivery receipt tests — pure logic, no DB
# ---------------------------------------------------------------------------

class DeliveryReceiptTests(unittest.TestCase):

    def test_make_receipt_delivered(self):
        from services.delivery_receipt import make_receipt
        r = make_receipt("art-123", "email", status="delivered")
        self.assertEqual(r.artifact_id, "art-123")
        self.assertEqual(r.channel, "email")
        self.assertEqual(r.status, "delivered")
        self.assertIsNotNone(r.delivered_at)
        self.assertIsNone(r.error)

    def test_make_receipt_failed(self):
        from services.delivery_receipt import make_receipt
        r = make_receipt("art-456", "crm", status="failed", error="timeout")
        self.assertEqual(r.status, "failed")
        self.assertEqual(r.error, "timeout")
        self.assertIsNone(r.delivered_at)

    def test_receipt_to_dict(self):
        from services.delivery_receipt import make_receipt
        r = make_receipt("art-789", "sms", status="delivered")
        d = r.to_dict()
        self.assertEqual(d["artifact_id"], "art-789")
        self.assertEqual(d["channel"], "sms")
        self.assertIn("created_at", d)


# ---------------------------------------------------------------------------
# 3. Approval queue — DB interactions mocked
# ---------------------------------------------------------------------------

class ApprovalQueueTests(unittest.TestCase):

    @patch("services.approval_queue.execute_query")
    def test_persist_artifact_calls_execute_query(self, mock_eq):
        from services.artifact import create_artifact
        from services.approval_queue import persist_artifact
        a = create_artifact(
            agent_id="tyler",
            business_key="aiphoneguy",
            artifact_type="note",
            audience="internal",
            intent="inform",
            content="Test.",
        )
        persist_artifact(a)
        mock_eq.assert_called_once()
        sql_arg = mock_eq.call_args[0][0]
        self.assertIn("INSERT INTO artifacts", sql_arg)

    @patch("services.approval_queue.execute_query")
    @patch("services.approval_queue.fetch_all", return_value=[])
    def test_approve_artifact_not_found_returns_false(self, mock_fa, mock_eq):
        from services.approval_queue import approve_artifact
        result = approve_artifact("nonexistent-id", "reviewer@x.com")
        self.assertFalse(result)

    @patch("services.approval_queue.execute_query")
    @patch("services.approval_queue.fetch_all")
    def test_approve_artifact_wrong_status_returns_false(self, mock_fa, mock_eq):
        from services.approval_queue import approve_artifact
        # Simulate artifact that is already 'delivered'
        mock_fa.return_value = [(
            "art-001", "tyler", "aiphoneguy", "email", "prospect",
            "nurture", "Hello.", "Subject", '["email"]', 0.9,
            "medium", True, '{}',
            datetime.datetime.utcnow(), "delivered",  # <-- already delivered
        )]
        result = approve_artifact("art-001", "reviewer")
        self.assertFalse(result)

    @patch("services.approval_queue.execute_query")
    @patch("services.approval_queue.fetch_all")
    def test_reject_artifact_success(self, mock_fa, mock_eq):
        from services.approval_queue import reject_artifact
        mock_fa.return_value = [(
            "art-002", "tyler", "aiphoneguy", "email", "prospect",
            "nurture", "Hello.", "Subject", '["email"]', 0.9,
            "medium", True, '{}',
            datetime.datetime.utcnow(), "pending_approval",
        )]
        result = reject_artifact("art-002", "reviewer", reason="Off-brand tone.")
        self.assertTrue(result)
        # Check UPDATE was called
        calls = [str(c) for c in mock_eq.call_args_list]
        self.assertTrue(any("UPDATE" in c for c in calls))


# ---------------------------------------------------------------------------
# 4. Dispatch — adapters mocked
# ---------------------------------------------------------------------------

class DispatchTests(unittest.TestCase):

    def test_dispatch_wrong_status_raises(self):
        from services.artifact import create_artifact
        from services.dispatch import dispatch_artifact
        a = create_artifact(
            agent_id="tyler",
            business_key="aiphoneguy",
            artifact_type="email",
            audience="prospect",
            intent="close",
            content="Hello.",
            confidence=0.5,
        )
        # Simulate it being escalated so dispatch should fail
        a.status = "pending_approval"
        with self.assertRaises(ValueError):
            dispatch_artifact(a)

    @patch("services.dispatch._update_artifact_status")
    @patch("services.dispatch.record_receipt")
    def test_dispatch_email_auto_approved(self, mock_rr, mock_update):
        from services.artifact import create_artifact
        from services.dispatch import dispatch_artifact, _CHANNEL_ADAPTERS
        from services.delivery_receipt import make_receipt

        mock_receipt = make_receipt("art-x", "email", status="delivered")
        mock_email = MagicMock(return_value=mock_receipt)

        a = create_artifact(
            agent_id="atlas",
            business_key="autointelligence",
            artifact_type="note",      # note → low risk
            audience="internal",
            intent="inform",
            content="Briefing note.",
            confidence=0.95,
            metadata={"contact_id": "test-contact-123"},  # required by email pre-validation
        )
        self.assertEqual(a.status, "auto_approved")
        a.channel_candidates = ["email"]  # force email channel

        import services.dispatch as _dispatch_mod
        original = _dispatch_mod._CHANNEL_ADAPTERS.get("email")
        try:
            _dispatch_mod._CHANNEL_ADAPTERS["email"] = mock_email
            receipt = dispatch_artifact(a)
        finally:
            if original is not None:
                _dispatch_mod._CHANNEL_ADAPTERS["email"] = original

        mock_email.assert_called_once_with(a)
        mock_rr.assert_called_once()
        self.assertEqual(receipt.status, "delivered")

    @patch("services.dispatch._update_artifact_status")
    @patch("services.dispatch.record_receipt")
    def test_dispatch_unknown_channel_uses_stub(self, mock_rr, mock_update):
        from services.artifact import create_artifact
        from services.dispatch import dispatch_artifact
        from services.delivery_receipt import make_receipt
        import services.dispatch as _dispatch_mod

        mock_receipt = make_receipt("art-y", "whatsapp", status="dispatched")
        mock_stub = MagicMock(return_value=mock_receipt)

        a = create_artifact(
            agent_id="atlas",
            business_key="autointelligence",
            artifact_type="note",
            audience="internal",
            intent="inform",
            content="Test.",
            confidence=0.95,
        )
        a.channel_candidates = ["whatsapp"]  # unknown channel

        # _CHANNEL_ADAPTERS.get("whatsapp", fallback_lambda) — fallback calls _dispatch_stub
        # Patch _dispatch_stub at module level so the default-lambda picks it up
        with patch.object(_dispatch_mod, "_dispatch_stub", mock_stub):
            receipt = dispatch_artifact(a)

        mock_stub.assert_called_once()
        self.assertEqual(receipt.status, "dispatched")


# ---------------------------------------------------------------------------
# 5. API endpoint tests
# ---------------------------------------------------------------------------

class ActivationAPITests(unittest.TestCase):

    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ.pop("API_KEYS", None)
        from config import runtime
        runtime.get_settings.cache_clear()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env_backup)
        from config import runtime
        runtime.get_settings.cache_clear()

    @patch("services.approval_queue.execute_query")
    def test_post_artifact_returns_artifact_id(self, mock_eq):
        from fastapi.testclient import TestClient
        import app as _app
        client = TestClient(_app.app)

        payload = {
            "agent_id": "atlas",
            "business_key": "autointelligence",
            "artifact_type": "note",
            "audience": "internal",
            "intent": "inform",
            "content": "Daily briefing complete.",
            "confidence": 0.95,
        }
        resp = client.post("/api/artifacts", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("artifact_id", data)
        self.assertIn("status", data)
        self.assertIn("risk_level", data)

    def test_post_artifact_invalid_type_422(self):
        from fastapi.testclient import TestClient
        import app as _app
        client = TestClient(_app.app)

        payload = {
            "agent_id": "tyler",
            "business_key": "aiphoneguy",
            "artifact_type": "INVALID_TYPE",
            "audience": "prospect",
            "intent": "nurture",
            "content": "Hello.",
        }
        resp = client.post("/api/artifacts", json=payload)
        self.assertEqual(resp.status_code, 422)

    @patch("services.approval_queue.fetch_all", return_value=[])
    def test_get_pending_returns_empty_list(self, mock_fa):
        from fastapi.testclient import TestClient
        import app as _app
        client = TestClient(_app.app)

        resp = client.get("/api/artifacts/pending")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["pending"], [])

    @patch("services.approval_queue.fetch_all", return_value=[])
    def test_get_escalated_returns_empty_list(self, mock_fa):
        from fastapi.testclient import TestClient
        import app as _app
        client = TestClient(_app.app)

        resp = client.get("/api/artifacts/escalated")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 0)

    def test_get_artifact_not_found_404(self):
        from fastapi.testclient import TestClient
        import app as _app

        with patch("services.approval_queue.fetch_all", return_value=[]):
            client = TestClient(_app.app)
            resp = client.get("/api/artifacts/no-such-id")
            self.assertEqual(resp.status_code, 404)

    @patch("services.approval_queue.fetch_all", return_value=[])
    def test_reject_artifact_not_found_404(self, mock_fa):
        from fastapi.testclient import TestClient
        import app as _app
        client = TestClient(_app.app)

        resp = client.post(
            "/api/artifacts/no-such-id/reject",
            json={"reason": "Off-brand."},
        )
        self.assertEqual(resp.status_code, 404)

    def test_list_artifacts_invalid_status_422(self):
        from fastapi.testclient import TestClient
        import app as _app
        client = TestClient(_app.app)

        resp = client.get("/api/artifacts?status=BOGUS_STATUS")
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
