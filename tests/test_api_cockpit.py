"""Tests for services/cockpit_stats.py (the GET /api/cockpit backing).

Covers the two things the cockpit endpoint promises:
  1. It returns the full contract shape with correct types.
  2. Every data source is fail-safe: a missing/erroring source degrades that
     field to null (and adds a health.notes string) rather than raising.

No test hits the network: the TP-daily reader, Twenty readiness, and the
watchdog snapshot are all patched. The TP-daily sample is generated with the
real build_block() writer so the parser is verified against the true format.
"""

import unittest
from unittest.mock import patch

from services import cockpit_stats
from services.tp_daily_engine import build_block


# A two-block state file (newest on top), exactly as team_principal_state.md holds it.
_ROWS_NEW = [
    ("AIPG", "Answering intent v3", 120, 100, 6, 2),
    ("AvI", "Dealer GM outreach", 80, 70, 3, 1),
]
_ROWS_OLD = [("AIPG", "old campaign", 10, 8, 1, 0)]
_BLOCK_NEW = build_block(_ROWS_NEW, ["P&P (no key set)"], "2026-08-05",
                         ["a@x.com", "b@y.com", "c@z.com"])
_BLOCK_OLD = build_block(_ROWS_OLD, [], "2026-08-04", [])
_SAMPLE_STATE = "**Last updated:** 2026-08-05\n" + _BLOCK_NEW + _BLOCK_OLD


class TpDailyParseTests(unittest.TestCase):
    def test_parses_latest_block(self):
        r = cockpit_stats.read_latest_tp_daily(reader=lambda _p: _SAMPLE_STATE)
        self.assertTrue(r["ok"])
        self.assertEqual(r["date"], "2026-08-05")           # newest block wins
        self.assertEqual(r["interested"], 3)                # 2 + 1, fail-closed count
        self.assertEqual(r["needs_review"], 3)              # three unclassified replies
        self.assertEqual(len(r["outbound"]), 2)             # only the newest block's rows
        first = r["outbound"][0]
        self.assertEqual(first["brand"], "AIPG")
        self.assertEqual(first["campaign"], "Answering intent v3")
        self.assertEqual(first["leads"], 120)
        self.assertEqual(first["sent"], 100)
        self.assertEqual(first["replies"], 6)
        self.assertEqual(first["interested"], 2)
        self.assertEqual(first["needs_review"], 0)          # not split per campaign

    def test_no_needs_eyes_line_is_zero(self):
        block = build_block([("AIPG", "c", 5, 5, 0, 0)], [], "2026-08-05", [])
        r = cockpit_stats.read_latest_tp_daily(reader=lambda _p: block)
        self.assertTrue(r["ok"])
        self.assertEqual(r["needs_review"], 0)
        self.assertEqual(r["interested"], 0)

    def test_reader_raises_degrades_to_null(self):
        def boom(_p):
            raise RuntimeError("github down")
        r = cockpit_stats.read_latest_tp_daily(reader=boom)
        self.assertFalse(r["ok"])
        self.assertIsNone(r["interested"])
        self.assertIsNone(r["needs_review"])
        self.assertTrue(r["note"])

    def test_empty_file_degrades_to_null(self):
        r = cockpit_stats.read_latest_tp_daily(reader=lambda _p: "")
        self.assertFalse(r["ok"])
        self.assertIsNone(r["interested"])
        self.assertTrue(r["note"])

    def test_no_block_degrades_to_null(self):
        r = cockpit_stats.read_latest_tp_daily(reader=lambda _p: "# some file\nno heartbeat here")
        self.assertFalse(r["ok"])
        self.assertIsNone(r["interested"])
        self.assertIn("no TP-daily block", r["note"])


class PipelineTests(unittest.TestCase):
    def test_no_workspace_configured_degrades_to_null(self):
        # twenty_ready False for every brand -> every workspace skipped, no network.
        with patch("tools.twenty.twenty_ready", return_value=False):
            r = cockpit_stats.read_pipeline()
        self.assertIsNone(r["pipeline_open_amount"])
        self.assertIsNone(r["pipeline_open_count"])
        self.assertIsNone(r["new_opps_per_week"])
        self.assertIsNone(r["velocity_days"])
        self.assertTrue(any("unknown" in n for n in r["notes"]))

    def test_open_amount_and_new_per_week_computed(self):
        from datetime import datetime, timezone
        recent = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        opps = [
            # open deal with $5,000 amount, created this week
            {"stage": "NEW", "amount": {"amountMicros": "5000000000"}, "createdAt": recent},
            # won/customer deal -> excluded from open pipeline, still counts as new-this-week
            {"stage": "CUSTOMER", "amount": {"amountMicros": "9000000000"}, "createdAt": recent},
            # open deal, no amount, old
            {"stage": "MEETING", "amount": None, "createdAt": "2020-01-01T00:00:00Z"},
        ]

        def only_avi_ready(biz_key):
            return biz_key == "autointelligence"

        with patch("tools.twenty.twenty_ready", side_effect=only_avi_ready), \
             patch("services.metric_connectors.twenty_opportunity_log._list_opportunities",
                   return_value=opps) as mock_list:
            r = cockpit_stats.read_pipeline()

        mock_list.assert_called_once_with("autointelligence")
        self.assertEqual(r["pipeline_open_amount"], 5000.0)   # customer deal excluded
        self.assertEqual(r["pipeline_open_count"], 2)          # NEW + MEETING open
        self.assertEqual(r["new_opps_per_week"], 2.0)          # the two `recent` deals
        self.assertIn("avi", r["wired_brands"])

    def test_list_error_is_skipped_not_raised(self):
        def only_avi_ready(biz_key):
            return biz_key == "autointelligence"

        def boom(_biz):
            raise RuntimeError("twenty 500")

        with patch("tools.twenty.twenty_ready", side_effect=only_avi_ready), \
             patch("services.metric_connectors.twenty_opportunity_log._list_opportunities",
                   side_effect=boom):
            r = cockpit_stats.read_pipeline()
        # No workspace ended up readable -> null pipeline + a note, never an exception.
        self.assertIsNone(r["pipeline_open_amount"])
        self.assertTrue(any("failed" in n for n in r["notes"]))


class WatchdogHealthTests(unittest.TestCase):
    def _patch_state(self, state):
        return patch("services.watchdog.current_state_json", return_value=state)

    def test_green_when_no_anomalies(self):
        with self._patch_state({"ok": True, "active_anomalies": [], "swept_at": "2026-08-05T00:00:00Z"}):
            status, swept, notes = cockpit_stats.read_watchdog_health()
        self.assertEqual(status, "green")
        self.assertEqual(swept, "2026-08-05T00:00:00Z")

    def test_warn_and_crit_mapping(self):
        with self._patch_state({"ok": True, "active_anomalies": [{"severity": "warn"}]}):
            self.assertEqual(cockpit_stats.read_watchdog_health()[0], "warn")
        with self._patch_state({"ok": True, "active_anomalies": [
                {"severity": "warn"}, {"severity": "critical"}]}):
            self.assertEqual(cockpit_stats.read_watchdog_health()[0], "crit")

    def test_unknown_on_store_error(self):
        with self._patch_state({"ok": False, "error": "db down", "active_anomalies": []}):
            self.assertEqual(cockpit_stats.read_watchdog_health()[0], "unknown")

    def test_unknown_on_exception(self):
        with patch("services.watchdog.current_state_json", side_effect=RuntimeError("boom")):
            self.assertEqual(cockpit_stats.read_watchdog_health()[0], "unknown")


class BuildCockpitContractTests(unittest.TestCase):
    def _assert_contract(self, c):
        # top-level keys
        for k in ("generated_at", "money", "outbound", "pipeline", "health", "generated_by"):
            self.assertIn(k, c)
        self.assertEqual(c["generated_by"], "api/cockpit")
        self.assertIsInstance(c["generated_at"], str)
        # money
        for k in ("interested_humans", "needs_review", "pipeline_open_amount",
                  "pipeline_open_count", "mrr_committed", "mrr_pipeline_weighted", "mrr_note"):
            self.assertIn(k, c["money"])
        self.assertEqual(c["money"]["mrr_committed"], 0)
        self.assertEqual(c["money"]["mrr_pipeline_weighted"], 0)
        self.assertIn("Phase 2", c["money"]["mrr_note"])
        # pipeline
        for k in ("new_opps_per_week", "coverage_ratio", "velocity_days"):
            self.assertIn(k, c["pipeline"])
        # health
        for k in ("watchdog", "last_daily", "notes"):
            self.assertIn(k, c["health"])
        self.assertIn(c["health"]["watchdog"], ("green", "warn", "crit", "unknown"))
        self.assertIsInstance(c["health"]["notes"], list)
        self.assertIsInstance(c["outbound"], list)

    def test_happy_path_shape_and_values(self):
        with patch("services.cockpit_stats._read_state_file", return_value=_SAMPLE_STATE), \
             patch("services.cockpit_stats.read_pipeline", return_value={
                 "pipeline_open_amount": 5000.0, "pipeline_open_count": 2,
                 "new_opps_per_week": 2.0, "coverage_ratio": None, "velocity_days": None,
                 "notes": ["coverage_ratio pending quarterly targets (not configured)"],
                 "wired_brands": ["avi"]}), \
             patch("services.cockpit_stats.read_watchdog_health",
                   return_value=("green", "2026-08-05T00:00:00Z", [])):
            c = cockpit_stats.build_cockpit()

        self._assert_contract(c)
        self.assertEqual(c["money"]["interested_humans"], 3)
        self.assertEqual(c["money"]["needs_review"], 3)
        self.assertEqual(c["money"]["pipeline_open_amount"], 5000.0)
        self.assertEqual(c["money"]["pipeline_open_count"], 2)
        self.assertEqual(c["pipeline"]["new_opps_per_week"], 2.0)
        self.assertEqual(c["health"]["last_daily"], "2026-08-05")
        self.assertEqual(len(c["outbound"]), 2)

    def test_all_sources_missing_degrades_to_null_never_raises(self):
        def boom_reader(_p):
            raise RuntimeError("github down")

        with patch("services.cockpit_stats._read_state_file", side_effect=boom_reader), \
             patch("tools.twenty.twenty_ready", return_value=False), \
             patch("services.watchdog.current_state_json", side_effect=RuntimeError("db down")):
            c = cockpit_stats.build_cockpit()  # must not raise

        self._assert_contract(c)
        # money numbers null, not fabricated
        self.assertIsNone(c["money"]["interested_humans"])
        self.assertIsNone(c["money"]["needs_review"])
        self.assertIsNone(c["money"]["pipeline_open_amount"])
        self.assertIsNone(c["money"]["pipeline_open_count"])
        self.assertIsNone(c["pipeline"]["new_opps_per_week"])
        self.assertIsNone(c["pipeline"]["velocity_days"])
        self.assertEqual(c["health"]["watchdog"], "unknown")
        self.assertIsNone(c["health"]["last_daily"])
        # every dead source left a breadcrumb
        self.assertTrue(len(c["health"]["notes"]) >= 3)
        # MRR placeholders are still honest zeros + the note
        self.assertEqual(c["money"]["mrr_committed"], 0)
        self.assertIn("Phase 2", c["money"]["mrr_note"])


class EndpointWiringTests(unittest.TestCase):
    """Verify the route is registered AND sits behind the dashboard Basic Auth,
    without standing up a live server."""

    def test_route_registered_and_auth_protected(self):
        import app
        self.assertIn("/api/cockpit", app._DASH_AUTH_EXACT)
        self.assertTrue(app._dashboard_path_is_protected("/api/cockpit"))
        paths = {getattr(r, "path", None) for r in app.app.routes}
        self.assertIn("/api/cockpit", paths)


if __name__ == "__main__":
    unittest.main()
