"""Tests for the Loops contact-tracking surface (tools/loops.py).

Covers the Sales-Desk deal-tracking additions:
    create_or_update_contact  -> PUT  /v1/contacts/update  (upsert by email)
    set_contact_properties    -> same upsert, property-only
    track_event               -> POST /v1/events           (touch logging)

We patch tools.loops.requests.request (the single HTTP chokepoint used by
_loops_request) to assert request shaping — method, URL, JSON body, Bearer auth —
without any live Loops call. We also prove the missing-key path degrades to a
clean error string instead of crashing.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from tools import loops


def _resp(*, status=200, json_body=None, text="", content=b"{}"):
    r = MagicMock()
    r.status_code = status
    r.text = text
    r.content = content
    if json_body is None:
        r.json.side_effect = ValueError("no json")
    else:
        r.json.return_value = json_body
    return r


class _KeyMixin(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        # Ensure a resolvable key for the "wd" business_key path.
        os.environ.pop("LOOPS_API_KEY", None)
        os.environ["LOOPS_API_KEY_WD"] = "test-key-123"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)


class UpsertContactTests(_KeyMixin):
    def test_builds_put_contacts_update_with_top_level_properties(self):
        with patch("tools.loops.requests.request",
                   return_value=_resp(json_body={"success": True, "id": "c_1"})) as req:
            out = loops.create_or_update_contact(
                "deal@acme.com",
                properties={"firstName": "Dana", "dealStage": "qualified", "dealValue": 5000},
                mailing_lists={"list_a": True},
            )
        self.assertEqual(out, {"success": True, "id": "c_1"})
        (method, url), kwargs = req.call_args
        self.assertEqual(method, "PUT")
        self.assertEqual(url, "https://app.loops.so/api/v1/contacts/update")
        body = kwargs["json"]
        # email + custom/standard props are TOP-LEVEL (not nested).
        self.assertEqual(body["email"], "deal@acme.com")
        self.assertEqual(body["firstName"], "Dana")
        self.assertEqual(body["dealStage"], "qualified")
        self.assertEqual(body["dealValue"], 5000)
        self.assertEqual(body["mailingLists"], {"list_a": True})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key-123")

    def test_email_only_upsert(self):
        with patch("tools.loops.requests.request",
                   return_value=_resp(json_body={"success": True, "id": "c_2"})) as req:
            loops.create_or_update_contact("solo@acme.com")
        _, kwargs = req.call_args
        self.assertEqual(kwargs["json"], {"email": "solo@acme.com"})

    def test_empty_email_rejected_without_http(self):
        with patch("tools.loops.requests.request") as req:
            out = loops.create_or_update_contact("")
        self.assertIsInstance(out, str)
        self.assertIn("ERROR", out)
        req.assert_not_called()

    def test_wrapper_returns_string(self):
        with patch("tools.loops.requests.request",
                   return_value=_resp(json_body={"success": True, "id": "c_3"})):
            out = loops.loops_upsert_contact.func("wrap@acme.com",
                                                  properties={"dealStage": "won"})
        self.assertIsInstance(out, str)
        self.assertIn("c_3", out)


class SetContactPropertiesTests(_KeyMixin):
    def test_property_only_update_hits_upsert(self):
        with patch("tools.loops.requests.request",
                   return_value=_resp(json_body={"success": True, "id": "c_4"})) as req:
            loops.set_contact_properties("prop@acme.com",
                                         {"lastTouchAt": "2026-06-30", "dealValue": None})
        (method, url), kwargs = req.call_args
        self.assertEqual(method, "PUT")
        self.assertEqual(url, "https://app.loops.so/api/v1/contacts/update")
        body = kwargs["json"]
        self.assertEqual(body["email"], "prop@acme.com")
        self.assertEqual(body["lastTouchAt"], "2026-06-30")
        # None is a legitimate reset value and must survive.
        self.assertIn("dealValue", body)
        self.assertIsNone(body["dealValue"])

    def test_empty_properties_rejected_without_http(self):
        with patch("tools.loops.requests.request") as req:
            out = loops.set_contact_properties("x@acme.com", {})
        self.assertIn("ERROR", out)
        req.assert_not_called()


class TrackEventTests(_KeyMixin):
    def test_builds_post_events_payload(self):
        with patch("tools.loops.requests.request",
                   return_value=_resp(json_body={"success": True})) as req:
            out = loops.track_event(
                "touch@acme.com",
                "call_made",
                event_properties={"duration": 12, "outcome": "connected"},
                contact_properties={"dealStage": "contacted"},
            )
        self.assertEqual(out, {"success": True})
        (method, url), kwargs = req.call_args
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://app.loops.so/api/v1/events")
        body = kwargs["json"]
        self.assertEqual(body["email"], "touch@acme.com")
        self.assertEqual(body["eventName"], "call_made")
        self.assertEqual(body["eventProperties"], {"duration": 12, "outcome": "connected"})
        # contact props are TOP-LEVEL on the event payload, not nested.
        self.assertEqual(body["dealStage"], "contacted")
        self.assertNotIn("contactProperties", body)

    def test_minimal_event_payload(self):
        with patch("tools.loops.requests.request",
                   return_value=_resp(json_body={"success": True})) as req:
            loops.track_event("min@acme.com", "email_sent")
        _, kwargs = req.call_args
        self.assertEqual(kwargs["json"], {"email": "min@acme.com", "eventName": "email_sent"})

    def test_missing_event_name_rejected_without_http(self):
        with patch("tools.loops.requests.request") as req:
            out = loops.track_event("x@acme.com", "")
        self.assertIn("ERROR", out)
        req.assert_not_called()

    def test_wrapper_returns_string(self):
        with patch("tools.loops.requests.request",
                   return_value=_resp(json_body={"success": True})):
            out = loops.loops_track_event.func("wrap@acme.com", "stage_change")
        self.assertIsInstance(out, str)
        self.assertIn("success", out)


class MissingKeyDegradesTests(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        os.environ.pop("LOOPS_API_KEY", None)
        os.environ.pop("LOOPS_API_KEY_WD", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_upsert_without_key_returns_error_no_http(self):
        with patch("tools.loops.requests.request") as req:
            out = loops.create_or_update_contact("x@acme.com", {"dealStage": "new"})
        self.assertIsInstance(out, str)
        self.assertIn("ERROR", out)
        self.assertIn("LOOPS_API_KEY_WD", out)
        req.assert_not_called()

    def test_track_event_without_key_returns_error_no_http(self):
        with patch("tools.loops.requests.request") as req:
            out = loops.track_event("x@acme.com", "call_made")
        self.assertIsInstance(out, str)
        self.assertIn("ERROR", out)
        req.assert_not_called()

    def test_set_properties_without_key_returns_error_no_http(self):
        with patch("tools.loops.requests.request") as req:
            out = loops.set_contact_properties("x@acme.com", {"dealStage": "new"})
        self.assertIn("ERROR", out)
        req.assert_not_called()

    def test_wrappers_degrade_to_error_string(self):
        with patch("tools.loops.requests.request") as req:
            self.assertIn("ERROR", loops.loops_upsert_contact.func("x@acme.com"))
            self.assertIn("ERROR", loops.loops_track_event.func("x@acme.com", "e"))
        req.assert_not_called()


if __name__ == "__main__":
    unittest.main()
