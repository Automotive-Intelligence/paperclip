"""Tests for the AIPG data-quality + channel-routing guardrails.

Boardroom send blocker (2026-07-01): the AIPG prospect list had ~1/3 no-email
contacts sitting in EMAIL sequences, duplicates, invalid phone area codes
(875/995/120), personal gmail/yahoo addresses, and company names parked in the
person-name field — and phone-first local trades were aimed at EMAIL when AIPG
SELLS an AI phone receptionist.

These tests prove the guardrails:
  * a contact with no validated BUSINESS email is excluded from the EMAIL lane
    (routed to SMS/CALL or excluded) and is never sent an email step,
  * duplicates are dropped,
  * invalid phones are flagged,
  * phone-first contacts get the SMS-lane tag; email-havers get the email tag,
  * company-in-name is cleaned so merge tags don't render a company fragment.

Runs without a database (enrollment_store falls back to an in-process store when
DATABASE_URL is unset).
"""

import os
import unittest
from unittest.mock import patch, MagicMock

from rivers.ai_phone_guy import data_quality as dq
from rivers.ai_phone_guy import workflow as randy_wf
from rivers.ai_phone_guy import enrollment_store


def _resp(status=200, payload=None):
    r = MagicMock()
    r.status_code = status
    r.text = ""
    r.json.return_value = payload if payload is not None else {}
    return r


class EmailValidationTests(unittest.TestCase):
    def test_business_email_is_valid(self):
        self.assertTrue(dq.is_valid_business_email("owner@acmeplumbing.com"))

    def test_free_email_is_not_a_business_email(self):
        for e in ("ricksplumbing@gmail.com", "allstarjohn1@yahoo.com",
                  "allstarbackflow@outlook.com"):
            self.assertTrue(dq.is_free_email(e), e)
            self.assertFalse(dq.is_valid_business_email(e), e)

    def test_missing_or_malformed_email_is_not_valid(self):
        for e in ("", None, "not-an-email", "a@b", "foo@bar."):
            self.assertFalse(dq.is_valid_business_email(e), repr(e))


class PhoneValidationTests(unittest.TestCase):
    def test_valid_dfw_area_codes_pass(self):
        # The real DFW codes seen in live data.
        for p in ("+19725550100", "9725550100", "(469) 555-0100",
                  "+1 940 555 0100", "2145550100", "8175550100"):
            self.assertTrue(dq.is_valid_na_phone(p), p)
            self.assertFalse(dq.is_bad_phone(p), p)

    def test_bogus_area_codes_flagged(self):
        # The exact bogus numbers found in live AIPG GHL data.
        for p in ("+19959012936", "+18759030549", "+18759134759", "+11208494548"):
            self.assertFalse(dq.is_valid_na_phone(p), p)
            self.assertTrue(dq.is_bad_phone(p), p)

    def test_n11_and_leading_one_area_codes_rejected(self):
        for p in ("2115550100", "9115550100", "0125550100", "1115550100"):
            self.assertFalse(dq.is_valid_na_phone(p), p)

    def test_absent_phone_is_not_bad_just_absent(self):
        self.assertFalse(dq.is_bad_phone(""))
        self.assertFalse(dq.is_bad_phone(None))
        self.assertFalse(dq.has_valid_phone(""))


class NameParsingTests(unittest.TestCase):
    def test_company_in_name_detected_and_cleaned(self):
        # Real shape: "Little Elm Dental Studio" split into first/last.
        c = {"firstName": "Little", "lastName": "Elm Dental Studio",
             "companyName": "Little Elm Dental Studio"}
        self.assertTrue(dq.looks_like_company_in_name(c))
        cleaned = dq.clean_name_fields(c)
        self.assertEqual(cleaned["firstName"], "")
        self.assertTrue(cleaned["_name_cleaned"])
        # Company preserved for the {businessName} merge tag.
        self.assertEqual(cleaned["companyName"], "Little Elm Dental Studio")

    def test_real_person_name_untouched(self):
        c = {"firstName": "Pat", "lastName": "Rivera", "companyName": "Acme Plumbing"}
        self.assertFalse(dq.looks_like_company_in_name(c))
        self.assertIs(dq.clean_name_fields(c), c)


class ChannelRoutingTests(unittest.TestCase):
    def test_business_email_routes_to_email_lane(self):
        c = {"email": "owner@acmehvac.com", "phone": "+19725550100"}
        self.assertEqual(dq.route_channel(c), dq.CHANNEL_EMAIL)
        self.assertEqual(dq.lane_tag_for_channel(dq.route_channel(c)), dq.EMAIL_LANE_TAG)

    def test_phone_first_no_email_routes_to_sms_lane(self):
        # No email but a valid phone -> SMS/CALL lane (matches AIPG's product).
        c = {"email": "", "phone": "+19725550100"}
        self.assertEqual(dq.route_channel(c), dq.CHANNEL_SMS)
        self.assertEqual(dq.lane_tag_for_channel(dq.route_channel(c)), dq.SMS_LANE_TAG)

    def test_free_email_with_phone_routes_to_sms_lane(self):
        # Personal gmail is treated as phone-first, not a business email target.
        c = {"email": "ricksplumbing@gmail.com", "phone": "+19725550100"}
        self.assertEqual(dq.route_channel(c), dq.CHANNEL_SMS)

    def test_no_reachable_channel_is_excluded(self):
        c = {"email": "", "phone": "+18759030549"}  # no email, bogus phone
        self.assertEqual(dq.route_channel(c), dq.CHANNEL_EXCLUDED)


class FindNewProspectsGuardrailTests(unittest.TestCase):
    """Integration: guardrails run inside Randy's enroll gate."""

    def setUp(self):
        randy_wf._enrolled.clear()
        enrollment_store._reset_for_tests()

    @patch.dict(os.environ, {"GHL_API_KEY": "k", "GHL_LOCATION_ID": "loc"}, clear=False)
    @patch("rivers.ai_phone_guy.workflow.requests.get")
    def test_mixed_batch_routes_and_excludes_correctly(self, mock_get):
        contacts = [
            # business email -> email lane
            {"id": "e1", "companyName": "Acme HVAC", "email": "owner@acmehvac.com",
             "phone": "+19725550100", "tags": ["tyler-prospect", "hvac"]},
            # no email, valid phone -> sms lane
            {"id": "s1", "companyName": "Rick Plumbing", "email": "",
             "phone": "+14695550101", "tags": ["tyler-prospect", "plumbing"]},
            # free email, valid phone -> sms lane
            {"id": "s2", "companyName": "All Star", "email": "allstar@gmail.com",
             "phone": "+19405550102", "tags": ["tyler-prospect", "roofing"]},
            # no email, bogus phone -> excluded
            {"id": "x1", "companyName": "Ghost Roofing", "email": "",
             "phone": "+18759030549", "tags": ["tyler-prospect", "roofing"]},
            # bad area code but has business email -> still email lane, phone flagged
            {"id": "e2", "companyName": "Prosper Dental", "email": "front@prosperdental.com",
             "phone": "+19959012936", "tags": ["tyler-prospect", "dental"]},
        ]
        mock_get.return_value = _resp(200, {"contacts": contacts, "meta": {}})

        tally = {}
        found = randy_wf._find_new_prospects(tally)
        by_id = {c["id"]: c for c in found}

        self.assertNotIn("x1", by_id, "no-email + bogus-phone must be excluded")
        self.assertEqual(by_id["e1"]["_channel"], dq.CHANNEL_EMAIL)
        self.assertEqual(by_id["e1"]["_lane_tag"], dq.EMAIL_LANE_TAG)
        self.assertEqual(by_id["s1"]["_channel"], dq.CHANNEL_SMS)
        self.assertEqual(by_id["s1"]["_lane_tag"], dq.SMS_LANE_TAG)
        self.assertEqual(by_id["s2"]["_channel"], dq.CHANNEL_SMS)
        self.assertEqual(by_id["e2"]["_channel"], dq.CHANNEL_EMAIL)

        self.assertEqual(tally["routed_email"], 2)
        self.assertEqual(tally["routed_sms"], 2)
        self.assertEqual(tally["no_reachable_channel_excluded"], 1)
        self.assertEqual(tally["bad_phone_flagged"], 2)  # x1 and e2

    @patch.dict(os.environ, {"GHL_API_KEY": "k", "GHL_LOCATION_ID": "loc"}, clear=False)
    @patch("rivers.ai_phone_guy.workflow.requests.get")
    def test_duplicates_are_dropped(self, mock_get):
        contacts = [
            {"id": "d1", "companyName": "Acme Plumbing", "email": "owner@acmeplumbing.com",
             "phone": "+19725550100", "tags": ["tyler-prospect", "plumbing"]},
            # same company+email+phone, different id -> duplicate
            {"id": "d2", "companyName": "Acme Plumbing", "email": "owner@acmeplumbing.com",
             "phone": "(972) 555-0100", "tags": ["tyler-prospect", "plumbing"]},
        ]
        mock_get.return_value = _resp(200, {"contacts": contacts, "meta": {}})

        tally = {}
        found = randy_wf._find_new_prospects(tally)
        self.assertEqual([c["id"] for c in found], ["d1"])
        self.assertEqual(tally["dupes_dropped"], 1)

    @patch.dict(os.environ, {"GHL_API_KEY": "k", "GHL_LOCATION_ID": "loc"}, clear=False)
    @patch("rivers.ai_phone_guy.workflow.requests.get")
    def test_company_in_name_cleaned_during_screen(self, mock_get):
        contacts = [
            {"id": "n1", "firstName": "Little", "lastName": "Elm Dental Studio",
             "companyName": "Little Elm Dental Studio", "email": "hi@littleelmdental.com",
             "phone": "+19725550100", "tags": ["tyler-prospect", "dental"]},
        ]
        mock_get.return_value = _resp(200, {"contacts": contacts, "meta": {}})
        tally = {}
        found = randy_wf._find_new_prospects(tally)
        self.assertEqual(found[0]["firstName"], "",
                         "company-in-name must be blanked so {firstName} won't render a company fragment")
        self.assertEqual(tally["names_cleaned"], 1)


class SendGateTests(unittest.TestCase):
    """A SMS/CALL-lane contact must NEVER be sent an EMAIL step."""

    def setUp(self):
        randy_wf._enrolled.clear()
        enrollment_store._reset_for_tests()

    @patch.dict(os.environ, {"GHL_API_KEY": "k", "GHL_LOCATION_ID": "loc"}, clear=False)
    @patch("rivers.ai_phone_guy.workflow._send_ghl_email")
    @patch("rivers.ai_phone_guy.workflow._send_ghl_sms")
    def test_email_step_suppressed_for_sms_lane_contact(self, mock_sms, mock_email):
        # A plumber routed to SMS: day 0 is SMS (sends), day 2 is EMAIL (must skip).
        randy_wf._enrolled["c-sms"] = {
            "vertical": "plumber",
            "channel": dq.CHANNEL_SMS,
            "last_step": -1,
            "contact": {"id": "c-sms", "firstName": "", "companyName": "Rick Plumbing",
                        "_channel": dq.CHANNEL_SMS},
        }
        randy_wf._send_sequence_step("c-sms", 0)   # SMS day 0
        randy_wf._send_sequence_step("c-sms", 2)   # EMAIL day 2 — must be skipped

        self.assertEqual(mock_sms.call_count, 1, "SMS step should send")
        self.assertEqual(mock_email.call_count, 0, "EMAIL step must NOT send for SMS-lane contact")
        # Cursor still advanced past the skipped email step.
        self.assertEqual(randy_wf._enrolled["c-sms"]["last_step"], 2)

    @patch.dict(os.environ, {"GHL_API_KEY": "k", "GHL_LOCATION_ID": "loc"}, clear=False)
    @patch("rivers.ai_phone_guy.workflow._send_ghl_email")
    @patch("rivers.ai_phone_guy.workflow._send_ghl_sms")
    def test_email_step_sends_for_email_lane_contact(self, mock_sms, mock_email):
        randy_wf._enrolled["c-em"] = {
            "vertical": "plumber",
            "channel": dq.CHANNEL_EMAIL,
            "last_step": 0,
            "contact": {"id": "c-em", "firstName": "Pat", "companyName": "Acme Plumbing",
                        "_channel": dq.CHANNEL_EMAIL},
        }
        randy_wf._send_sequence_step("c-em", 2)  # EMAIL day 2
        self.assertEqual(mock_email.call_count, 1, "EMAIL step should send for email-lane contact")


if __name__ == "__main__":
    unittest.main()
