"""tests/test_content_coverage.py — Publishing Coverage % connector (CMO north-star KPI)."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from services.metric_connectors.content_coverage import compute_coverage, ryg

_UTC = timezone.utc
NOW = datetime(2026, 8, 9, 12, 0, tzinfo=_UTC)  # window = 08-02 .. 08-09

STUDIO = {"brands": {
    "automotive_intelligence": {"enabled": True, "posts_per_run": 7,
                                "platforms": ["linkedin", "x", "facebook", "instagram"]},
    "worship_digital": {"enabled": True, "posts_per_run": 7,
                        "platforms": ["facebook", "linkedin", "instagram", "x"]},
    "ai_phone_guy": {"enabled": True, "posts_per_run": 7, "platforms": ["facebook", "instagram"]},
    "agent_empire": {"enabled": True, "posts_per_run": 7, "platforms": ["facebook", "instagram"]},
}}

# WD has NO instagram connected -> IG is a connection gap, excluded from the denominator.
SLIP = {"zernio_accounts": {
    "autointelligence": {"linkedin": "1", "x": "2", "facebook": "3", "instagram": "4"},
    "worshipdigital": {"linkedin": "1", "facebook": "3", "x": "2"},
    "aiphoneguy": {"facebook": "3", "instagram": "4"},
    "agentempire": {"facebook": "3", "instagram": "4"},
}}


def _row(brand, platform, day, entry_point="studio", content_id="c"):
    return json.dumps({"brand": brand, "platform": platform, "entry_point": entry_point,
                       "content_id": content_id, "scheduled_for": f"2026-08-{day:02d}T18:00:00+00:00"})


def _registry(*rows):
    return "\n".join(rows) + "\n"


class TestCoverage(unittest.TestCase):
    def setUp(self):
        rows = [
            # avi: fb 3 days, ig 3 days, li 1, twitter(->x) 1
            _row("avi", "facebook", 3), _row("avi", "facebook", 4), _row("avi", "facebook", 5),
            _row("avi", "instagram", 3), _row("avi", "instagram", 4), _row("avi", "instagram", 5),
            _row("avi", "linkedin", 4), _row("avi", "twitter", 4),
            # a BACKFILL row on a new day must NOT count
            _row("avi", "facebook", 2, entry_point="backfill", content_id="backfill_pre_pipe"),
            # OUT OF WINDOW (07-20) must NOT count
            json.dumps({"brand": "avi", "platform": "facebook", "entry_point": "studio",
                        "content_id": "c", "scheduled_for": "2026-07-20T18:00:00+00:00"}),
            # wd: fb 3 days; wd_legacy_cd fb 1 day (folds into wd) -> wd fb = 4 distinct days
            _row("wd", "facebook", 3), _row("wd", "facebook", 4), _row("wd", "facebook", 5),
            _row("wd_legacy_cd", "facebook", 6),
            # wd instagram exists but IG is a CONNECTION GAP -> excluded from denom
            _row("wd", "instagram", 3),
            # founder channel -> excluded entirely
            _row("founder", "linkedin", 4),
            # aipg fb 2, ig 2
            _row("aipg", "facebook", 4), _row("aipg", "facebook", 6),
            _row("aipg", "instagram", 4), _row("aipg", "instagram", 6),
            # agent_empire fb 4, ig 4
            *[_row("agent_empire", "facebook", d) for d in (3, 4, 5, 6)],
            *[_row("agent_empire", "instagram", d) for d in (3, 4, 5, 6)],
        ]
        self.readings = compute_coverage(_registry(*rows), STUDIO, SLIP, NOW)
        self.by_brand = {r.brand: r for r in self.readings}

    def test_avi_coverage_and_twitter_maps_to_x(self):
        r = self.by_brand["avi"]
        # ship = fb3 + ig3 + li1 + x1 = 8 ; int = 7*4 = 28 -> 28.6%
        self.assertAlmostEqual(r.value_numeric, 28.6, places=1)
        self.assertEqual(r.raw_payload["platforms"]["x"]["shipped_days"], 1)  # twitter counted as x
        self.assertEqual(r.raw_payload["platforms"]["facebook"]["shipped_days"], 3)  # backfill+OOW excluded

    def test_wd_connection_gap_excluded_and_legacy_folds_in(self):
        r = self.by_brand["wd"]
        self.assertIn("instagram", r.raw_payload["connection_gaps"])
        self.assertNotIn("instagram", r.raw_payload["platforms"])  # gap not in denominator
        self.assertEqual(r.raw_payload["platforms"]["facebook"]["shipped_days"], 4)  # wd_legacy_cd folded in
        # ship = fb4 + li0 + x0 = 4 ; int = 7*3 = 21 -> 19.0%
        self.assertAlmostEqual(r.value_numeric, 19.0, places=1)

    def test_aipg_and_agent_empire(self):
        self.assertAlmostEqual(self.by_brand["aipg"].value_numeric, 28.6, places=1)   # 4/14
        self.assertAlmostEqual(self.by_brand["agent_empire"].value_numeric, 57.1, places=1)  # 8/14

    def test_founder_and_bookd_not_scored(self):
        self.assertNotIn("founder", self.by_brand)
        self.assertNotIn("bookd", self.by_brand)  # not enabled in studio config

    def test_portfolio_reading(self):
        port = self.by_brand[None]
        # ship = 8+4+4+8 = 24 ; int = 28+21+14+14 = 77 -> 31.2%
        self.assertAlmostEqual(port.value_numeric, 31.2, places=1)
        self.assertEqual(port.raw_payload["scope"], "portfolio")

    def test_empty_registry_is_no_data_not_false_zero(self):
        readings = compute_coverage("", STUDIO, SLIP, NOW)
        for r in readings:
            self.assertEqual(r.status, "no_data")
            self.assertIsNone(r.value_numeric)  # never a false 0%

    def test_registry_with_rows_but_none_for_brand_is_real_zero(self):
        # aipg has zero posts but the signal loaded -> a REAL 0%, status ok
        rows = _registry(_row("avi", "facebook", 4))
        by_brand = {r.brand: r for r in compute_coverage(rows, STUDIO, SLIP, NOW)}
        self.assertEqual(by_brand["aipg"].status, "ok")
        self.assertEqual(by_brand["aipg"].value_numeric, 0.0)


class TestRyg(unittest.TestCase):
    SPEC = {"target": 95, "threshold_yellow": 85, "threshold_red": 70}

    def test_green_yellow_red_and_no_data(self):
        self.assertEqual(ryg(97.0, self.SPEC), "green")
        self.assertEqual(ryg(95.0, self.SPEC), "green")
        self.assertEqual(ryg(90.0, self.SPEC), "yellow")
        self.assertEqual(ryg(85.0, self.SPEC), "yellow")
        self.assertEqual(ryg(33.3, self.SPEC), "red")   # the real portfolio number reads RED, not green
        self.assertEqual(ryg(None, self.SPEC), "no_data")


if __name__ == "__main__":
    unittest.main()
