"""Tests for services/handoff_consumer.

The DB layer is patched out — these tests verify the consumer's logic:
payload parsing, drain orchestration, failure isolation, and the
stale-handoff query shape.
"""

import unittest
from unittest.mock import patch

from services import handoff_consumer


class ParsePayloadTests(unittest.TestCase):
    def test_dict_passthrough(self):
        self.assertEqual(handoff_consumer._parse_payload({"a": 1}), {"a": 1})

    def test_json_string(self):
        self.assertEqual(
            handoff_consumer._parse_payload('{"a": 1, "b": "two"}'),
            {"a": 1, "b": "two"},
        )

    def test_bytes_json(self):
        self.assertEqual(
            handoff_consumer._parse_payload(b'{"x": 9}'),
            {"x": 9},
        )

    def test_garbage_returns_empty(self):
        self.assertEqual(handoff_consumer._parse_payload("not json"), {})
        self.assertEqual(handoff_consumer._parse_payload(None), {})
        self.assertEqual(handoff_consumer._parse_payload(12345), {})


class ClaimPendingHandoffsTests(unittest.TestCase):
    @patch("services.handoff_consumer.fetch_all")
    def test_claim_parses_rows(self, mock_fetch):
        mock_fetch.return_value = [
            (
                42, "cockpit_bridge", "callingdigital", "cockpit_flag",
                '{"target": "Client Marketing", "what": "ship X"}',
                "high", "2026-05-17T10:00:00Z",
            ),
        ]
        result = handoff_consumer.claim_pending_handoffs("sofia", limit=5)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], 42)
        self.assertEqual(result[0]["payload"], {"target": "Client Marketing", "what": "ship X"})
        self.assertEqual(result[0]["priority"], "high")
        # Confirm UPDATE...RETURNING SQL was used with FOR UPDATE SKIP LOCKED
        called_sql = mock_fetch.call_args[0][0]
        self.assertIn("UPDATE agent_handoffs", called_sql)
        self.assertIn("FOR UPDATE SKIP LOCKED", called_sql)
        self.assertIn("RETURNING", called_sql)

    @patch("services.handoff_consumer.fetch_all")
    def test_claim_empty_when_no_rows(self, mock_fetch):
        mock_fetch.return_value = []
        self.assertEqual(handoff_consumer.claim_pending_handoffs("sofia"), [])

    @patch("services.handoff_consumer.fetch_all")
    def test_claim_returns_empty_on_db_error(self, mock_fetch):
        from services.errors import DatabaseError
        mock_fetch.side_effect = DatabaseError("claim", "boom")
        self.assertEqual(handoff_consumer.claim_pending_handoffs("sofia"), [])


class DrainTests(unittest.TestCase):
    @patch("services.handoff_consumer.claim_pending_handoffs")
    def test_drain_empty(self, mock_claim):
        mock_claim.return_value = []
        stats = handoff_consumer.drain_handoffs_for_agent("sofia")
        self.assertEqual(stats["claimed"], 0)
        self.assertEqual(stats["completed"], 0)
        self.assertEqual(stats["failed"], 0)

    @patch("services.handoff_consumer.complete_handoff")
    @patch("services.handoff_consumer.claim_pending_handoffs")
    def test_drain_executes_each(self, mock_claim, mock_complete):
        mock_claim.return_value = [
            {"id": 1, "payload": {"what": "A"}, "priority": "medium"},
            {"id": 2, "payload": {"what": "B"}, "priority": "low"},
        ]
        calls = []

        def fake_exec(agent_name, h):
            calls.append((agent_name, h["id"]))
            return "OK"

        stats = handoff_consumer.drain_handoffs_for_agent("sofia", executor=fake_exec)
        self.assertEqual(stats["claimed"], 2)
        self.assertEqual(stats["completed"], 2)
        self.assertEqual(stats["failed"], 0)
        self.assertEqual(calls, [("sofia", 1), ("sofia", 2)])
        self.assertEqual(mock_complete.call_count, 2)

    @patch("services.handoff_consumer.fail_handoff")
    @patch("services.handoff_consumer.complete_handoff")
    @patch("services.handoff_consumer.claim_pending_handoffs")
    def test_drain_isolates_failures(self, mock_claim, mock_complete, mock_fail):
        mock_claim.return_value = [
            {"id": 1, "payload": {"what": "A"}, "priority": "medium"},
            {"id": 2, "payload": {"what": "B"}, "priority": "medium"},
            {"id": 3, "payload": {"what": "C"}, "priority": "medium"},
        ]

        def flaky(agent_name, h):
            if h["id"] == 2:
                raise RuntimeError("simulated crash")
            return "OK"

        stats = handoff_consumer.drain_handoffs_for_agent("marcus", executor=flaky)
        self.assertEqual(stats["claimed"], 3)
        self.assertEqual(stats["completed"], 2)
        self.assertEqual(stats["failed"], 1)
        # The failed one was marked failed, the others completed
        self.assertEqual(mock_fail.call_count, 1)
        self.assertEqual(mock_complete.call_count, 2)
        # The failure didn't stop iteration
        statuses = [r["status"] for r in stats["results"]]
        self.assertEqual(statuses, ["complete", "failed", "complete"])


class StaleQueryTests(unittest.TestCase):
    @patch("services.handoff_consumer.fetch_all")
    def test_stale_returns_parsed_rows(self, mock_fetch):
        mock_fetch.return_value = [
            (
                101, "sofia", "callingdigital", "cockpit_flag", "high",
                "2026-05-17T08:00:00Z", "2026-05-17T08:01:00Z",
            ),
        ]
        rows = handoff_consumer.get_stale_handoffs(stale_minutes=30)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], 101)
        self.assertEqual(rows[0]["to_agent"], "sofia")
        # Confirm the interval threshold was passed as parameter, not interpolated
        called_args = mock_fetch.call_args[0][1]
        self.assertEqual(called_args, ("30",))


class TaskDescriptionTests(unittest.TestCase):
    def test_full_flag_renders_all_fields(self):
        handoff = {
            "id": 7,
            "priority": "high",
            "payload": {
                "target": "Client Marketing",
                "what": "Generate P&P pre-sale email #3",
                "why_now": "Pre-sale opens 5/29",
                "by_when": "EOD today",
                "posted_by": "Michael / Pit Wall",
                "posted": "2026-05-17 14:00 CST",
                "_routing": {
                    "reasoning": "Sofia owns Calling Digital client content; P&P is a CD client",
                },
            },
        }
        desc = handoff_consumer._build_flag_task_description(handoff)
        self.assertIn("Generate P&P pre-sale email #3", desc)
        self.assertIn("Pre-sale opens 5/29", desc)
        self.assertIn("EOD today", desc)
        self.assertIn("Michael / Pit Wall", desc)
        self.assertIn("Sofia owns Calling Digital client content", desc)
        # Execution rules must be present
        self.assertIn("Empty is better than fake", desc)
        self.assertIn("Paper & Purpose", desc)

    def test_missing_fields_do_not_crash(self):
        handoff = {"id": 1, "priority": "low", "payload": {}}
        desc = handoff_consumer._build_flag_task_description(handoff)
        # Renders empty fields without raising
        self.assertIn("WHAT:", desc)
        self.assertIn("WHY NOW:", desc)


if __name__ == "__main__":
    unittest.main()
