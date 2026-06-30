"""Tests for the AIPG/Randy enrollment fix (fix/aipg-randy-tag-contract).

Guards the two-part bug PR #121 only half-closed:

  1. TAG CONTRACT. Tyler stamps the BARE `tyler-prospect` tag + a SEPARATE
     industry tag (plumbing / roofing / dental / hvac / personal-injury-law).
     Randy used to search COMPOUND keys (`tyler-prospect-plumber`) that exist on
     ZERO live contacts, so it enrolled nobody. Randy must now match the bare
     tag and derive the vertical from the industry tag.

  2. PERSISTENCE. Enrollment state used to live in a process-local dict that
     reset on every restart, so already-enrolled contacts re-appeared as "new".
     It must now survive a simulated restart.

These tests run without a database: enrollment_store transparently falls back
to an in-process store when DATABASE_URL is unset, which is the path exercised
here. The DB path uses the identical code with the fallback swapped for
execute_query/fetch_all.
"""

import os
import unittest
from unittest.mock import patch, MagicMock

from rivers.ai_phone_guy import workflow as randy_wf
from rivers.ai_phone_guy import enrollment_store
from rivers.ai_phone_guy.sequences import vertical_for_tags, TYLER_PROSPECT_TAG


def _resp(status=200, payload=None):
    r = MagicMock()
    r.status_code = status
    r.text = ""
    r.json.return_value = payload if payload is not None else {}
    return r


class IndustryTagMappingTests(unittest.TestCase):
    """The real-data industry-tag → vertical map."""

    def test_bare_tagged_plumbing_contact_maps_to_plumber(self):
        # (a) a contact tagged like live data is matched + mapped.
        tags = ["tyler-prospect", "plumbing", "cold-email"]
        self.assertEqual(vertical_for_tags(tags), "plumber")

    def test_each_live_industry_tag_maps(self):
        cases = {
            "plumbing": "plumber",
            "plumber": "plumber",
            "plumbing/hvac": "plumber",
            "roofing": "roofer",
            "hvac": "hvac",
            "hvac/plumbing": "hvac",
            "dental": "dental",
            "personal-injury-law": "lawyer",
        }
        for industry, vertical in cases.items():
            self.assertEqual(
                vertical_for_tags(["tyler-prospect", industry, "cold-email"]),
                vertical,
                f"{industry!r} should map to {vertical!r}",
            )

    def test_no_industry_tag_returns_empty(self):
        # Pipeline-only tags (no trade) → no sequence to fire.
        self.assertEqual(vertical_for_tags(["tyler-prospect", "cold-email", "aiphoneguy", "dfw"]), "")

    def test_case_and_whitespace_insensitive(self):
        self.assertEqual(vertical_for_tags(["tyler-prospect", " Plumbing "]), "plumber")


class FindNewProspectsTagContractTests(unittest.TestCase):
    """Randy's search now matches bare-tagged contacts; compound matching is gone."""

    def setUp(self):
        randy_wf._enrolled.clear()
        enrollment_store._reset_for_tests()

    @patch.dict(os.environ, {"GHL_API_KEY": "k", "GHL_LOCATION_ID": "loc"}, clear=False)
    @patch("rivers.ai_phone_guy.workflow.requests.get")
    def test_bare_tagged_contact_is_matched_and_mapped(self, mock_get):
        # (a) contact tagged ["tyler-prospect","plumbing","cold-email"] is found.
        contact = {
            "id": "c-plumb",
            "firstName": "Pat",
            "lastName": "Doe",
            "tags": ["tyler-prospect", "plumbing", "cold-email"],
        }
        mock_get.return_value = _resp(200, {"contacts": [contact], "meta": {}})

        found = randy_wf._find_new_prospects()

        self.assertEqual(len(found), 1, "Bare tyler-prospect contact must be matched")
        self.assertEqual(found[0]["id"], "c-plumb")
        self.assertEqual(found[0]["_vertical"], "plumber")
        # And Randy searched the BARE tag, not a compound key.
        sent_params = mock_get.call_args.kwargs["params"]
        self.assertEqual(sent_params["query"], TYLER_PROSPECT_TAG)

    @patch.dict(os.environ, {"GHL_API_KEY": "k", "GHL_LOCATION_ID": "loc"}, clear=False)
    @patch("rivers.ai_phone_guy.workflow.requests.get")
    def test_old_compound_only_matching_no_longer_strands(self, mock_get):
        # (b) A population of bare-tagged contacts (no compound tag anywhere) —
        # the old code matched 0 of these. The fix matches all with a known trade.
        contacts = [
            {"id": "p1", "tags": ["tyler-prospect", "plumbing", "cold-email"]},
            {"id": "r1", "tags": ["tyler-prospect", "roofing", "cold-email"]},
            {"id": "h1", "tags": ["tyler-prospect", "hvac", "cold-email"]},
            {"id": "d1", "tags": ["tyler-prospect", "dental", "cold-email"]},
            {"id": "l1", "tags": ["tyler-prospect", "personal-injury-law", "cold-email"]},
            # No recognized trade — correctly skipped (not stranded as an error).
            {"id": "x1", "tags": ["tyler-prospect", "cold-email", "dfw"]},
        ]
        mock_get.return_value = _resp(200, {"contacts": contacts, "meta": {}})

        found = randy_wf._find_new_prospects()
        found_ids = {c["id"] for c in found}

        self.assertEqual(found_ids, {"p1", "r1", "h1", "d1", "l1"})
        verts = {c["id"]: c["_vertical"] for c in found}
        self.assertEqual(verts, {"p1": "plumber", "r1": "roofer", "h1": "hvac", "d1": "dental", "l1": "lawyer"})

    @patch.dict(os.environ, {"GHL_API_KEY": "k", "GHL_LOCATION_ID": "loc"}, clear=False)
    @patch("rivers.ai_phone_guy.workflow.requests.get")
    def test_already_sequence_active_is_skipped(self, mock_get):
        contact = {"id": "c2", "tags": ["tyler-prospect", "hvac", "sequence-active"]}
        mock_get.return_value = _resp(200, {"contacts": [contact], "meta": {}})
        self.assertEqual(randy_wf._find_new_prospects(), [])


class EnrollmentPersistenceTests(unittest.TestCase):
    """Enrollment state survives a simulated process restart."""

    def setUp(self):
        randy_wf._enrolled.clear()
        enrollment_store._reset_for_tests()

    @patch.dict(os.environ, {"GHL_API_KEY": "k", "GHL_LOCATION_ID": "loc"}, clear=False)
    @patch("rivers.ai_phone_guy.workflow.requests.post")
    def test_enrollment_persists_across_restart(self, mock_post):
        # (c) Enroll a contact, simulate a restart (clear the in-process cache),
        # and assert the durable store still knows it's enrolled — so it is NOT
        # re-found as "new".
        mock_post.return_value = _resp(200)
        contact = {"id": "c-persist", "_vertical": "plumber", "firstName": "Pat", "lastName": "Doe",
                   "tags": ["tyler-prospect", "plumbing", "cold-email"]}

        durable = randy_wf._enroll_contact(contact)
        self.assertTrue(durable)
        self.assertTrue(enrollment_store.is_enrolled("c-persist"))

        # --- simulate process restart: the volatile cache is gone ---
        randy_wf._enrolled.clear()
        self.assertNotIn("c-persist", randy_wf._enrolled)

        # Durable store still remembers it.
        self.assertTrue(enrollment_store.is_enrolled("c-persist"),
                        "Enrollment must survive a restart (durable store)")

        # And _find_new_prospects must NOT re-surface it as new.
        with patch("rivers.ai_phone_guy.workflow.requests.get") as mock_get:
            mock_get.return_value = _resp(200, {"contacts": [contact], "meta": {}})
            found = randy_wf._find_new_prospects()
        self.assertEqual(found, [], "Already-enrolled contact must not re-appear as new after restart")

    @patch.dict(os.environ, {"GHL_API_KEY": "k", "GHL_LOCATION_ID": "loc"}, clear=False)
    @patch("rivers.ai_phone_guy.workflow.requests.post")
    def test_all_enrollments_round_trips(self, mock_post):
        mock_post.return_value = _resp(200)
        contact = {"id": "c-rt", "_vertical": "dental", "firstName": "Dee", "lastName": "Doe", "tags": []}
        randy_wf._enroll_contact(contact)

        store = enrollment_store.all_enrollments()
        self.assertIn("c-rt", store)
        self.assertEqual(store["c-rt"]["vertical"], "dental")


if __name__ == "__main__":
    unittest.main()
