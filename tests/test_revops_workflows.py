"""Tests for the three RevOps river workflows (Randy / Brenda / Darrell).

These guard the fix for the "agents heartbeat, Randy enrollment leaks, retired
CRMs still called" production incident (CRO RED flag 2026-06-27T21:45Z):

  1. randy_run / brenda_run / darrell_run must RETURN a real per-run summary
     (>=200 chars) so _avo_wrap_run in app.py persists it instead of falling
     back to the ~55-char heartbeat string.
  2. Randy's _enroll_contact must write the 'sequence-active' tag via the
     dedicated GHL Add-Tags endpoint (POST /contacts/{id}/tags) and report
     success/failure truthfully — the prior PUT /contacts/{id} silently no-oped,
     which is why 29 tyler-pushed contacts had ZERO enrollment tags.
  3. Brenda (Attio, retired) and Darrell (HubSpot, retiring) must NOT issue live
     requests to their retired CRMs from the scheduled read path.

The HTTP layer is patched out — no live API calls are made.
"""

import os
import unittest
from unittest.mock import patch, MagicMock

from rivers.ai_phone_guy import workflow as randy_wf
from rivers.calling_digital import workflow as brenda_wf
from rivers.automotive_intelligence import workflow as darrell_wf


def _ok_response(status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.text = ""
    resp.json.return_value = {}
    return resp


class RandyEnrollmentTagTests(unittest.TestCase):
    """The core fix: enrollment tags actually land in GHL."""

    def setUp(self):
        randy_wf._enrolled.clear()
        randy_wf._stats.update({"enrolled": 0, "hot_leads": 0, "messages_sent": 0})

    @patch.dict(os.environ, {"GHL_API_KEY": "test-key", "GHL_LOCATION_ID": "loc"}, clear=False)
    @patch("rivers.ai_phone_guy.workflow.requests.post")
    def test_enroll_uses_add_tags_endpoint_and_confirms(self, mock_post):
        mock_post.return_value = _ok_response(200)
        contact = {"id": "c1", "_vertical": "plumber", "firstName": "Pat", "lastName": "Doe", "tags": ["tyler-prospect-plumber"]}

        durable = randy_wf._enroll_contact(contact)

        self.assertTrue(durable, "Successful tag write must report durable=True")
        # Must hit the dedicated Add-Tags endpoint, NOT PUT /contacts/{id}.
        url = mock_post.call_args.args[0] if mock_post.call_args.args else mock_post.call_args.kwargs.get("url", "")
        self.assertTrue(url.endswith("/contacts/c1/tags"), f"Wrong endpoint: {url}")
        body = mock_post.call_args.kwargs["json"]
        self.assertEqual(body, {"tags": ["sequence-active"]})

    @patch.dict(os.environ, {"GHL_API_KEY": "test-key", "GHL_LOCATION_ID": "loc"}, clear=False)
    @patch("rivers.ai_phone_guy.workflow.requests.post")
    def test_enroll_reports_leak_on_failure(self, mock_post):
        mock_post.return_value = _ok_response(422)  # GHL rejects
        contact = {"id": "c2", "_vertical": "hvac", "firstName": "Sam", "lastName": "Roe", "tags": ["tyler-prospect-hvac"]}

        durable = randy_wf._enroll_contact(contact)

        self.assertFalse(durable, "Failed tag write must report durable=False (the leak signal)")

    @patch.dict(os.environ, {}, clear=True)
    def test_enroll_dry_run_no_http(self):
        # No GHL key => dry-run; treated as durable, no requests made.
        contact = {"id": "c3", "_vertical": "roofer", "firstName": "Lee", "lastName": "Fox", "tags": []}
        with patch("rivers.ai_phone_guy.workflow.requests.post") as mock_post:
            durable = randy_wf._enroll_contact(contact)
            mock_post.assert_not_called()
        self.assertTrue(durable)


class RunSummaryTests(unittest.TestCase):
    """All three RevOps runs must return a real >=200-char summary (no heartbeat)."""

    @patch.dict(os.environ, {}, clear=True)
    def test_randy_run_returns_real_summary(self):
        result = randy_wf.randy_run()
        self.assertIsInstance(result, str)
        self.assertGreaterEqual(len(result), 200, f"Randy summary too short ({len(result)}): {result!r}")
        self.assertIn("RANDY RUN", result)

    @patch.dict(os.environ, {}, clear=True)
    def test_brenda_run_returns_real_summary(self):
        result = brenda_wf.brenda_run()
        self.assertIsInstance(result, str)
        self.assertGreaterEqual(len(result), 200, f"Brenda summary too short ({len(result)}): {result!r}")
        self.assertIn("BRENDA RUN", result)

    @patch.dict(os.environ, {}, clear=True)
    def test_darrell_run_returns_real_summary(self):
        result = darrell_wf.darrell_run()
        self.assertIsInstance(result, str)
        self.assertGreaterEqual(len(result), 200, f"Darrell summary too short ({len(result)}): {result!r}")
        self.assertIn("DARRELL RUN", result)


class RetiredCrmGuardTests(unittest.TestCase):
    """Brenda (Attio) and Darrell (HubSpot) must not touch their retired CRMs."""

    @patch.dict(os.environ, {"ATTIO_API_KEY": "should-not-be-used"}, clear=False)
    @patch("rivers.calling_digital.workflow.requests.post")
    @patch("rivers.calling_digital.workflow.requests.get")
    def test_brenda_does_not_query_attio(self, mock_get, mock_post):
        contacts = brenda_wf._find_new_contacts()
        self.assertEqual(contacts, [])
        mock_get.assert_not_called()
        mock_post.assert_not_called()

    @patch.dict(os.environ, {"HUBSPOT_API_KEY": "should-not-be-used"}, clear=False)
    @patch("rivers.automotive_intelligence.workflow.requests.post")
    @patch("rivers.automotive_intelligence.workflow.requests.get")
    def test_darrell_does_not_query_hubspot(self, mock_get, mock_post):
        dealers = darrell_wf._find_verified_dealers()
        self.assertEqual(dealers, [])
        mock_get.assert_not_called()
        mock_post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
