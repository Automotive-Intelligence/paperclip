"""Tests for the three RevOps river workflows (Randy / Brenda / Darrell).

These guard the clean-rebase fix for the "agents heartbeat, Randy enrollment
leaks, retiring CRM still called" production incident (CRO RED flag
2026-06-27T21:45Z):

  1. randy_run / brenda_run / darrell_run must RETURN a real per-run summary
     (>=200 chars) so _avo_wrap_run in app.py persists it instead of falling
     back to the ~55-char heartbeat string.
  2. Randy's _enroll_contact must write the 'sequence-active' tag via the
     dedicated GHL Add-Tags endpoint (POST /contacts/{id}/tags) and report
     success/failure truthfully — the prior PUT /contacts/{id} silently no-oped
     (tags-only body ignored), which is why 29 tyler-pushed contacts had ZERO
     enrollment tags.
  3. Brenda reads the LIVE Twenty WD workspace (PR #82 migrated her off the
     retired Attio CRM). Her test asserts the Twenty path: no Attio call, the
     reader returns real contacts. She is intentionally NOT stubbed.
  4. Darrell (HubSpot, retiring; AvI not yet on Twenty) must NOT issue live
     requests to HubSpot from the scheduled read path — [RETIRED-CRM] guard
     returns [] (the known residual gap).

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
    """The core fix: enrollment tags actually land in GHL via the Add-Tags endpoint."""

    def setUp(self):
        randy_wf._enrolled.clear()
        randy_wf._stats.update({"enrolled": 0, "hot_leads": 0, "messages_sent": 0})

    @patch.dict(os.environ, {"GHL_API_KEY": "test-key", "GHL_LOCATION_ID": "loc"}, clear=False)
    @patch("rivers.ai_phone_guy.workflow.requests.post")
    @patch("rivers.ai_phone_guy.workflow.requests.put")
    def test_enroll_uses_add_tags_endpoint_and_confirms(self, mock_put, mock_post):
        mock_post.return_value = _ok_response(200)
        contact = {"id": "c1", "_vertical": "plumber", "firstName": "Pat", "lastName": "Doe", "tags": ["tyler-prospect-plumber"]}

        durable = randy_wf._enroll_contact(contact)

        self.assertTrue(durable, "Successful tag write must report durable=True")
        # Must hit the dedicated Add-Tags endpoint via POST, NOT PUT /contacts/{id}.
        mock_put.assert_not_called()
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


class BrendaTwentyReaderTests(unittest.TestCase):
    """Brenda reads the LIVE Twenty WD workspace (PR #82). She is NOT stubbed.

    The fix asserts the Twenty path: no Attio call, the reader returns real
    contacts. (#119 regressed this to `return []` — that stub is dropped here.)
    """

    @patch.dict(os.environ, {"ATTIO_API_KEY": "should-not-be-used"}, clear=False)
    @patch("tools.twenty._workspace_config", return_value=("https://wd.twenty.test", "wd-key"))
    @patch("tools.twenty.twenty_ready", return_value=True)
    @patch("rivers.calling_digital.workflow.requests.get")
    def test_brenda_reads_twenty_returns_real_contacts(self, mock_get, _ready, _cfg):
        # Pretend the workflow is already past baseline seeding so net-new
        # contacts are actually returned (not swallowed by the seed run).
        original_state = dict(brenda_wf._state)
        brenda_wf._state["initialized_at"] = "2026-06-01T00:00:00"
        brenda_wf._state["seen_ids"] = []
        try:
            resp = _ok_response(200)
            resp.json.return_value = {
                "data": {
                    "people": [
                        {
                            "id": "p1",
                            "name": {"firstName": "Dana", "lastName": "Lopez"},
                            "emails": {"primaryEmail": "dana@example.com"},
                            "phones": {"primaryPhoneNumber": "+15125550100"},
                            "jobTitle": "Med Spa Owner",
                        },
                        {
                            "id": "p2",
                            "name": {"firstName": "Rob", "lastName": "Vance"},
                            "emails": {"primaryEmail": "rob@example.com"},
                            "phones": {},
                            "jobTitle": "Attorney",
                        },
                    ]
                }
            }
            mock_get.return_value = resp

            contacts = brenda_wf._find_new_contacts()

            # Real contacts returned from the Twenty reader (NOT []).
            self.assertEqual(len(contacts), 2)
            self.assertEqual({c["id"] for c in contacts}, {"p1", "p2"})
            self.assertEqual(contacts[0]["firstName"], "Dana")
            # The Twenty REST endpoint was queried, not Attio.
            called_url = mock_get.call_args.args[0] if mock_get.call_args.args else mock_get.call_args.kwargs.get("url", "")
            self.assertIn("/rest/people", called_url)
            self.assertNotIn("attio.com", called_url)
        finally:
            brenda_wf._state.clear()
            brenda_wf._state.update(original_state)


class DarrellRetiredCrmGuardTests(unittest.TestCase):
    """Darrell (HubSpot, retiring; AvI not on Twenty) must not touch HubSpot."""

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
