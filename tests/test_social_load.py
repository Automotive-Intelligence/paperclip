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


if __name__ == "__main__":
    unittest.main()
