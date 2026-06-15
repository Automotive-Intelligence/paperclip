"""Integration tests for tools/buffer.py against Buffer's GraphQL API.

These hit Buffer live — run via:
  doppler run -- python -m unittest tests.test_buffer_tools

Paper & Purpose / Calling Digital team Buffer account.
Real channel + organization IDs (3 P&P channels).
"""

from __future__ import annotations

import json
import os
import unittest

from tools.buffer import (
    buffer_list_ideas,
    buffer_list_organizations,
    buffer_list_posts,
)

# Known Buffer state for the Calling Digital team account.
PP_ORG_ID = "69ed6e4bb3eb4d0e37ba2f6a"
PP_CHANNELS = [
    "6a205be0c687a22dd4584d1f",
    "6a205c0cc687a22dd4584db5",
    "6a205c29c687a22dd4584e07",
]


@unittest.skipUnless(
    os.environ.get("BUFFER_API_KEY"),
    "BUFFER_API_KEY missing — run via `doppler run -- python -m unittest tests.test_buffer_tools`",
)
class BufferListOrganizationsTests(unittest.TestCase):
    """Sanity floor: account.organizations query has always worked.
    Verifies the wrapper plumbing + auth + JSON parsing are intact.
    """

    def test_returns_at_least_one_org(self):
        raw = buffer_list_organizations.func()
        self.assertNotIn("ERROR", raw, f"unexpected error: {raw[:200]}")
        data = json.loads(raw)
        self.assertIsInstance(data, list)
        self.assertGreaterEqual(len(data), 1)
        self.assertIn("id", data[0])
        self.assertIn("channelCount", data[0])


@unittest.skipUnless(
    os.environ.get("BUFFER_API_KEY"),
    "BUFFER_API_KEY missing — run via doppler run",
)
class BufferListPostsTests(unittest.TestCase):
    """The fix target: buffer_list_posts must use posts(input: PostsInput).

    Pre-fix: returns 'ERROR: Buffer GraphQL: Unknown argument "channelId" on field "Query.posts"'.
    Post-fix: returns JSON with edges/posts payload.
    """

    def test_no_old_schema_error(self):
        raw = buffer_list_posts.func(PP_CHANNELS[0], "draft", 3)
        self.assertNotIn(
            "Unknown argument",
            raw,
            f"Wrapper still uses removed channelId arg: {raw[:300]}",
        )

    def test_returns_edges_payload(self):
        raw = buffer_list_posts.func(PP_CHANNELS[0], "draft", 3)
        self.assertNotIn("ERROR", raw, f"unexpected error: {raw[:300]}")
        data = json.loads(raw)
        # Accept either {posts: {edges: [...]}} (raw passthrough) or [...] (edge list).
        if isinstance(data, dict) and "posts" in data:
            self.assertIn("edges", data["posts"])
            self.assertIsInstance(data["posts"]["edges"], list)
        elif isinstance(data, dict) and "edges" in data:
            self.assertIsInstance(data["edges"], list)
        elif isinstance(data, list):
            pass  # list of edges directly
        else:
            self.fail(f"unexpected shape: {type(data).__name__} keys={list(data.keys()) if isinstance(data, dict) else 'n/a'}")

    def test_smoke_three_channels(self):
        """All 3 P&P channel IDs return data (not all need posts, but none should error)."""
        for ch in PP_CHANNELS:
            with self.subTest(channel=ch):
                raw = buffer_list_posts.func(ch, "draft", 3)
                self.assertNotIn("ERROR", raw, f"channel {ch}: {raw[:200]}")
                self.assertNotIn("Unknown argument", raw, f"channel {ch}: schema regression")


@unittest.skipUnless(
    os.environ.get("BUFFER_API_KEY"),
    "BUFFER_API_KEY missing — run via doppler run",
)
class BufferListIdeasTests(unittest.TestCase):
    """buffer_list_ideas wraps Buffer's ideas(input: IdeasListInput!).

    Current state: API key may lack Ideas scope (plan-tier limit). The wrapper
    must return a CLEAN error string in that case (not crash, not return raw
    GraphQL errors). When plan/scope upgrades, the same call returns edges.
    """

    def test_returns_data_or_clean_forbidden(self):
        raw = buffer_list_ideas.func(PP_ORG_ID, 5)
        self.assertIsInstance(raw, str)
        # Either FORBIDDEN-ish error string, or valid JSON with edges.
        if raw.startswith("ERROR"):
            # Wrapper translated the error cleanly — that's the expected current state.
            self.assertTrue(
                "Not authorized" in raw
                or "FORBIDDEN" in raw
                or "scope" in raw.lower(),
                f"unexpected error shape: {raw[:300]}",
            )
        else:
            data = json.loads(raw)
            # When scope lands, ideas returns edges
            self.assertTrue(
                ("ideas" in data and "edges" in data["ideas"]) or "edges" in data,
                f"unexpected shape: {raw[:200]}",
            )


if __name__ == "__main__":
    unittest.main()
