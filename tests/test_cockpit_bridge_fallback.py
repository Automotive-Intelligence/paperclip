"""Tests for the cockpit-bridge fallback visibility + skipped-prefix work.

DB layer is patched out. These tests verify:
- _is_skipped_target prefix-matches and is robust to whitespace
- get_recent_fallback_routings parses payload + handles DB error
"""

import unittest
from unittest.mock import patch

from services import cockpit_bridge


class SkippedPrefixTests(unittest.TestCase):
    def test_exact_match_skipped(self):
        self.assertTrue(cockpit_bridge._is_skipped_target("Build & Tech"))

    def test_prefix_match_skipped(self):
        # The historic bug: this used to slip past the exact-match check
        # and got routed via GPT-4o → Sofia + Marcus, then was never
        # consumed because the agents didn't read agent_handoffs.
        self.assertTrue(cockpit_bridge._is_skipped_target(
            "Build & Tech — Sofia + Marcus tier-1 production sprint"
        ))

    def test_whitespace_tolerated(self):
        self.assertTrue(cockpit_bridge._is_skipped_target("  Build & Tech  "))

    def test_other_targets_not_skipped(self):
        self.assertFalse(cockpit_bridge._is_skipped_target("Client Marketing"))
        self.assertFalse(cockpit_bridge._is_skipped_target("Internal Marketing"))
        self.assertFalse(cockpit_bridge._is_skipped_target("Revenue & Sales"))

    def test_empty_or_none_not_skipped(self):
        self.assertFalse(cockpit_bridge._is_skipped_target(""))
        self.assertFalse(cockpit_bridge._is_skipped_target(None))


class FallbackQueryTests(unittest.TestCase):
    @patch("services.cockpit_bridge.fetch_all")
    def test_fallback_filter_in_sql(self, mock_fetch):
        mock_fetch.return_value = []
        cockpit_bridge.get_recent_fallback_routings(limit=5)
        called_args = mock_fetch.call_args[0]
        sql = called_args[0]
        params = called_args[1]
        # SQL must filter via LIKE on the payload field
        self.assertIn("ah.payload LIKE", sql)
        # Limit passed through
        self.assertEqual(params[1], 5)
        # Pattern matches the JSON shape we actually write
        self.assertIn('"source": "fallback"', params[0])

    @patch("services.cockpit_bridge.fetch_all")
    def test_fallback_parses_routing_payload(self, mock_fetch):
        mock_fetch.return_value = [
            (
                "hash123", "brand_rules.md", "Internal Marketing",
                "2026-05-17 14:00", 42, "2026-05-17T14:01:00Z",
                '{"target": "Internal Marketing", "_routing": {"source": "fallback", "reasoning": "fallback: completion error"}}',
                "complete",
            ),
        ]
        rows = cockpit_bridge.get_recent_fallback_routings(limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["handoff_id"], 42)
        self.assertEqual(rows[0]["target"], "Internal Marketing")
        self.assertEqual(rows[0]["routing"]["source"], "fallback")
        self.assertIn("completion error", rows[0]["routing"]["reasoning"])

    @patch("services.cockpit_bridge.fetch_all")
    def test_fallback_returns_empty_on_db_error(self, mock_fetch):
        from services.errors import DatabaseError
        mock_fetch.side_effect = DatabaseError("fallback_query", "boom")
        self.assertEqual(cockpit_bridge.get_recent_fallback_routings(), [])

    @patch("services.cockpit_bridge.fetch_all")
    def test_fallback_handles_dict_payload(self, mock_fetch):
        # When the DB driver returns payload already as a dict (e.g., jsonb),
        # not as a string. The parser should handle both.
        mock_fetch.return_value = [
            (
                "h", "f.md", "X", "p", 1, "t",
                {"_routing": {"source": "fallback", "reasoning": "r"}},
                "pending",
            ),
        ]
        rows = cockpit_bridge.get_recent_fallback_routings()
        self.assertEqual(rows[0]["routing"]["source"], "fallback")


if __name__ == "__main__":
    unittest.main()
