"""tests/test_social_load.py — unit tests for the one social loader (file 121 Phase 1)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest


class TestUtm(unittest.TestCase):
    def test_add_utm_basic(self):
        from tools.social_load import add_utm
        out = add_utm("https://theaiphoneguy.com/blog/missed-call",
                      platform="facebook", brand="aipg",
                      content_id="missed-call", entry_point="blog_engine", slot="1")
        self.assertIn("utm_source=facebook", out)
        self.assertIn("utm_medium=social", out)
        self.assertIn("utm_campaign=aipg_missed-call", out)
        self.assertIn("utm_content=blog_engine-1", out)
        self.assertTrue(out.startswith("https://theaiphoneguy.com/blog/missed-call?"))

    def test_add_utm_preserves_existing_query(self):
        from tools.social_load import add_utm
        out = add_utm("https://x.co/p?ref=abc", "twitter", "avi", "c1", "adhoc", "0")
        self.assertIn("ref=abc", out)
        self.assertIn("utm_source=twitter", out)

    def test_tag_links_rewrites_all_urls_in_text(self):
        from tools.social_load import tag_links
        text = "Read https://a.com/x and https://b.com/y today"
        out = tag_links(text, "linkedin", "wd", "post9", "studio", "2")
        self.assertEqual(out.count("utm_source=linkedin"), 2)

    def test_tag_links_leaves_plain_text_alone(self):
        from tools.social_load import tag_links
        text = "Call (817) 670-9689 today. worshipdigital.co"
        self.assertEqual(tag_links(text, "facebook", "wd", "c", "studio", "0"), text)


class TestRegistry(unittest.TestCase):
    def test_append_registry_writes_jsonl(self):
        from tools.social_load import append_registry
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "reg.jsonl")
            os.environ["SOCIAL_REGISTRY_PATH"] = path
            try:
                append_registry({"brand": "avi", "platform": "twitter", "post_id": "p1"})
                append_registry({"brand": "avi", "platform": "linkedin", "post_id": "p2"})
                rows = [json.loads(l) for l in open(path)]
            finally:
                del os.environ["SOCIAL_REGISTRY_PATH"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["post_id"], "p1")
        self.assertIn("ts", rows[0])


class TestRouting(unittest.TestCase):
    def test_own_brand_routes_zernio(self):
        from tools.social_load import route_for_brand
        self.assertEqual(route_for_brand("avi"), "zernio")
        self.assertEqual(route_for_brand("Automotive Intelligence"), "zernio")

    def test_pp_routes_buffer(self):
        from tools.social_load import route_for_brand
        self.assertEqual(route_for_brand("paperandpurpose"), "buffer")
        self.assertEqual(route_for_brand("Paper & Purpose"), "buffer")

    def test_wd_blocked_until_rename(self):
        from tools.social_load import route_for_brand, WdBlockedError
        with self.assertRaises(WdBlockedError):
            route_for_brand("wd", cfg={"wd_rename_done": False})
        self.assertEqual(route_for_brand("wd", cfg={"wd_rename_done": True}), "zernio")

    def test_bookd_has_no_rail(self):
        from tools.social_load import route_for_brand, NoRailError
        with self.assertRaises(NoRailError):
            route_for_brand("bookd")


class TestQueueGuard(unittest.TestCase):
    def test_conflict_same_platform_same_local_day(self):
        from tools.social_load import find_conflicts
        existing = [{
            "_id": "z1", "status": "scheduled",
            "scheduledFor": "2026-07-14T11:30:00.000Z",   # 06:30 CDT
            "platforms": [{"platform": "facebook", "accountId": {"_id": "acct9"}}],
        }]
        hits = find_conflicts(existing, "facebook", "2026-07-14", account_id="acct9")
        self.assertEqual([h["_id"] for h in hits], ["z1"])

    def test_no_conflict_different_day_after_tz_conversion(self):
        from tools.social_load import find_conflicts
        existing = [{
            "_id": "z2", "status": "scheduled",
            "scheduledFor": "2026-07-15T00:00:00.000Z",   # 19:00 CDT on the 14th
            "platforms": [{"platform": "instagram", "accountId": "acct9"}],
        }]
        self.assertEqual(find_conflicts(existing, "instagram", "2026-07-15", "acct9"), [])
        self.assertEqual(len(find_conflicts(existing, "instagram", "2026-07-14", "acct9")), 1)

    def test_non_scheduled_rows_ignored(self):
        from tools.social_load import find_conflicts
        existing = [{"_id": "z3", "status": "failed",
                     "scheduledFor": "2026-07-14T12:00:00.000Z",
                     "platforms": [{"platform": "facebook", "accountId": "a"}]}]
        self.assertEqual(find_conflicts(existing, "facebook", "2026-07-14", "a"), [])


def _fake_rails(existing_zernio=None, existing_buffer=None, fetch=None, upload=None):
    calls = {"publish": [], "draft": [], "fetch": [], "upload": []}

    def zernio_list():
        return existing_zernio or []

    def zernio_publish(content, platforms, account_ids, scheduled_for, media_urls, timezone):
        calls["publish"].append(dict(content=content, platforms=platforms,
                                     account_ids=account_ids, scheduled_for=scheduled_for,
                                     media_urls=media_urls, timezone=timezone))
        return {"_id": f"zp{len(calls['publish'])}", "status": "scheduled"}

    def buffer_list(channel_id):
        return existing_buffer or []

    def buffer_draft(business_key, text, media_urls_csv):
        calls["draft"].append(dict(business_key=business_key, text=text, media=media_urls_csv))
        return json.dumps([{"channel_id": "c1", "post": {"id": "bp1", "status": "draft"}}])

    def media_fetch(url):
        calls["fetch"].append(url)
        if fetch is not None:
            return fetch(url)                     # test-supplied (e.g. raise on 404)
        return (b"\x89PNG\r\n\x1a\n", "image/png")

    def media_upload(file_bytes, filename, mime_type):
        calls["upload"].append(dict(filename=filename, mime_type=mime_type,
                                    nbytes=len(file_bytes)))
        if upload is not None:
            return upload(file_bytes, filename, mime_type)
        return f"https://media.zernio.com/{filename}"

    return {"zernio_list": zernio_list, "zernio_publish": zernio_publish,
            "buffer_list": buffer_list, "buffer_draft": buffer_draft,
            "media_fetch": media_fetch, "media_upload": media_upload}, calls


class TestLoadJobs(unittest.TestCase):
    def setUp(self):
        # EVERY test in this class writes registry rows through load_jobs; without
        # this blanket override a commit-path test leaks fake rows into the REAL
        # registry (it happened: 3x zp1 rows, purged 2026-07-13).
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = os.environ.get("SOCIAL_REGISTRY_PATH")
        os.environ["SOCIAL_REGISTRY_PATH"] = os.path.join(self._tmp.name, "r.jsonl")

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("SOCIAL_REGISTRY_PATH", None)
        else:
            os.environ["SOCIAL_REGISTRY_PATH"] = self._prev
        self._tmp.cleanup()

    def _job(self, **kw):
        from tools.social_load import PostJob
        base = dict(brand="avi", platform="twitter", content="Read https://a.io/p now",
                    scheduled_for="2026-07-16T07:00:00", content_id="c9",
                    entry_point="adhoc", account_id="acct1")
        base.update(kw)
        return PostJob(**base)

    def test_dry_run_calls_no_rail(self):
        from tools.social_load import load_jobs
        rails, calls = _fake_rails()
        res = load_jobs([self._job()], commit=False, rails=rails)
        self.assertEqual(res[0]["action"], "dry-run")
        self.assertEqual(calls["publish"], [])

    def test_commit_schedules_tags_utm_and_registers(self):
        from tools.social_load import load_jobs
        rails, calls = _fake_rails()
        with tempfile.TemporaryDirectory() as d:
            os.environ["SOCIAL_REGISTRY_PATH"] = os.path.join(d, "r.jsonl")
            try:
                res = load_jobs([self._job()], commit=True, rails=rails)
                rows = [json.loads(l) for l in open(os.environ["SOCIAL_REGISTRY_PATH"])]
            finally:
                del os.environ["SOCIAL_REGISTRY_PATH"]
        self.assertEqual(res[0]["action"], "scheduled")
        self.assertIn("utm_source=twitter", calls["publish"][0]["content"])
        self.assertEqual(rows[0]["post_id"], "zp1")
        self.assertEqual(rows[0]["utm_campaign"], "avi_c9")

    def test_conflict_blocks_without_allow_stack(self):
        from tools.social_load import load_jobs
        existing = [{"_id": "old", "status": "scheduled",
                     "scheduledFor": "2026-07-16T12:00:00.000Z",
                     "platforms": [{"platform": "twitter", "accountId": "acct1"}]}]
        rails, calls = _fake_rails(existing_zernio=existing)
        res = load_jobs([self._job()], commit=True, rails=rails)
        self.assertEqual(res[0]["action"], "conflict")
        self.assertEqual(calls["publish"], [])
        res2 = load_jobs([self._job()], commit=True, allow_stack=True, rails=rails)
        self.assertEqual(res2[0]["action"], "scheduled")

    def test_wd_blocked(self):
        from tools.social_load import load_jobs
        rails, calls = _fake_rails()
        res = load_jobs([self._job(brand="wd")], commit=True, rails=rails,
                        cfg={"wd_rename_done": False})
        self.assertEqual(res[0]["action"], "blocked")
        self.assertEqual(calls["publish"], [])

    def test_wd_flows_after_rename(self):
        from tools.social_load import load_jobs
        rails, calls = _fake_rails()
        res = load_jobs([self._job(brand="wd", platform="facebook", account_id="cd1")],
                        commit=False, rails=rails, cfg={"wd_rename_done": True})
        self.assertEqual(res[0]["action"], "dry-run")

    def test_pp_goes_to_buffer_as_draft(self):
        from tools.social_load import load_jobs
        rails, calls = _fake_rails()
        with tempfile.TemporaryDirectory() as d:
            os.environ["SOCIAL_REGISTRY_PATH"] = os.path.join(d, "r.jsonl")
            try:
                res = load_jobs([self._job(brand="paperandpurpose", platform="instagram",
                                           business_key="paperandpurpose", account_id=None)],
                                commit=True, rails=rails)
            finally:
                del os.environ["SOCIAL_REGISTRY_PATH"]
        self.assertEqual(res[0]["action"], "drafted")
        self.assertEqual(len(calls["draft"]), 1)
        self.assertEqual(calls["publish"], [])


class TestMediaRehost(unittest.TestCase):
    """Blog-engine bug (3 WD posts failed): external media URLs were handed to
    Zernio un-rehosted, so a source URL that 404s by publish time (days later)
    failed the post SILENTLY. The loader must re-host external media onto
    media.zernio.com AT SCHEDULE TIME, and fail LOUD when a source is unreachable
    instead of scheduling a job with dead media."""

    def setUp(self):
        # Same registry isolation as TestLoadJobs: the commit path writes a row.
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = os.environ.get("SOCIAL_REGISTRY_PATH")
        os.environ["SOCIAL_REGISTRY_PATH"] = os.path.join(self._tmp.name, "r.jsonl")

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("SOCIAL_REGISTRY_PATH", None)
        else:
            os.environ["SOCIAL_REGISTRY_PATH"] = self._prev
        self._tmp.cleanup()

    def _job(self, **kw):
        from tools.social_load import PostJob
        base = dict(brand="avi", platform="twitter", content="New post https://a.io/p",
                    scheduled_for="2026-07-16T07:00:00", content_id="hero9",
                    entry_point="blog_engine", account_id="acct1")
        base.update(kw)
        return PostJob(**base)

    def test_external_media_is_rehosted_before_scheduling(self):
        from tools.social_load import load_jobs
        rails, calls = _fake_rails()
        src = "https://worshipdigital.co/blog/agency-pricing-hero.png"
        res = load_jobs([self._job(media_urls=[src])], commit=True, rails=rails)
        self.assertEqual(res[0]["action"], "scheduled")
        # source was downloaded exactly once and re-uploaded to the CDN
        self.assertEqual(calls["fetch"], [src])
        self.assertEqual(len(calls["upload"]), 1)
        # Zernio received the persistent CDN url, NOT the fragile external one
        scheduled_media = calls["publish"][0]["media_urls"]
        self.assertEqual(len(scheduled_media), 1)
        self.assertTrue(scheduled_media[0].startswith("https://media.zernio.com/"))
        self.assertNotIn("worshipdigital.co", scheduled_media[0])
        # registry logs what was ACTUALLY scheduled (the persistent url)
        rows = [json.loads(l) for l in open(os.environ["SOCIAL_REGISTRY_PATH"])]
        self.assertEqual(rows[0]["media_url"], scheduled_media[0])

    def test_media_already_on_zernio_cdn_passes_through_untouched(self):
        from tools.social_load import load_jobs
        rails, calls = _fake_rails()
        cdn = "https://media.zernio.com/abc123.png"
        res = load_jobs([self._job(media_urls=[cdn])], commit=True, rails=rails)
        self.assertEqual(res[0]["action"], "scheduled")
        self.assertEqual(calls["fetch"], [])     # not re-downloaded
        self.assertEqual(calls["upload"], [])    # not re-uploaded
        self.assertEqual(calls["publish"][0]["media_urls"], [cdn])

    def test_unreachable_media_raises_loudly_and_does_not_schedule(self):
        from tools.social_load import load_jobs, MediaUnreachableError

        def dead(url):
            raise RuntimeError("HTTP 404")

        rails, calls = _fake_rails(fetch=dead)
        bad = "https://worshipdigital.co/blog/agency-pricing-hero.png"
        with self.assertRaises(MediaUnreachableError) as ctx:
            load_jobs([self._job(media_urls=[bad])], commit=True, rails=rails)
        msg = str(ctx.exception)
        self.assertIn(bad, msg)                  # names the offending URL
        self.assertIn("hero9", msg)              # names the content_id
        self.assertEqual(calls["publish"], [])   # nothing was scheduled

    def test_dry_run_does_not_touch_media(self):
        # Preview must not upload bytes or hit the network; re-host is commit-only.
        from tools.social_load import load_jobs
        rails, calls = _fake_rails()
        res = load_jobs([self._job(media_urls=["https://ex.com/x.png"])],
                        commit=False, rails=rails)
        self.assertEqual(res[0]["action"], "dry-run")
        self.assertEqual(calls["fetch"], [])
        self.assertEqual(calls["upload"], [])


class TestCli(unittest.TestCase):
    def test_cli_dry_run(self):
        import subprocess, sys
        import tempfile as tf
        jobs = [{"brand": "avi", "platform": "twitter", "content": "hi https://a.io",
                 "scheduled_for": "2026-07-16T07:00:00", "content_id": "c",
                 "entry_point": "adhoc", "account_id": "a1"}]
        with tf.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(jobs, f); path = f.name
        env = {**os.environ, "SOCIAL_LOAD_FAKE_RAILS": "1"}
        out = subprocess.run([sys.executable, "tools/social_load.py", path],
                             capture_output=True, text=True, env=env,
                             cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        os.unlink(path)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("dry-run", out.stdout)


class TestScoreboard(unittest.TestCase):
    def test_registry_window_and_dedupe(self):
        from tools.social_scoreboard import load_registry
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "r.jsonl")
            os.environ["SOCIAL_REGISTRY_PATH"] = path
            try:
                with open(path, "w") as f:
                    f.write(json.dumps({"post_id": "a", "scheduled_for": "2026-07-14T07:00:00"}) + "\n")
                    f.write(json.dumps({"post_id": "a", "scheduled_for": "2026-07-17T07:00:00"}) + "\n")  # re-time
                    f.write(json.dumps({"post_id": "b", "scheduled_for": "2026-07-01T07:00:00"}) + "\n")  # out of window
                rows = load_registry("2026-07-10", "2026-07-20")
            finally:
                del os.environ["SOCIAL_REGISTRY_PATH"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["scheduled_for"], "2026-07-17T07:00:00")

    def test_table_renders_without_rail_calls_for_buffer(self):
        from tools.social_scoreboard import build_table
        calls = []
        rows = [{"post_id": "bp1", "rail": "buffer", "brand": "paperandpurpose",
                 "platform": "instagram", "scheduled_for": "2026-07-16T12:00:00",
                 "content_id": "c", "utm_campaign": "paperandpurpose_c"}]
        table = build_table(rows, {}, lambda **kw: calls.append(kw))
        self.assertEqual(calls, [])
        self.assertIn("paperandpurpose", table)


if __name__ == "__main__":
    unittest.main()
