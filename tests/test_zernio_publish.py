"""Unit tests for tools/zernio.publish_to_zernio response unwrapping.

Regression guard: Zernio's `POST /posts` returns the created post nested under a
"post" key ({"post": {"_id", "status", ...}}). publish_to_zernio must return the
post OBJECT (per its docstring), so direct callers (studio_publish.py ledger/console,
services/dispatch.py) read the real `_id`/`status` instead of None.

Run:  python -m unittest tests.test_zernio_publish
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.zernio import publish_to_zernio


class TestPublishToZernioUnwrap(unittest.TestCase):
    ACCT = "6a1111111111111111111111"

    def _call(self, fake_response):
        with patch("tools.zernio._zernio_request", return_value=fake_response) as m:
            result = publish_to_zernio(
                content="hello world",
                platforms=["twitter"],
                account_ids=[self.ACCT],
                scheduled_for="2026-07-11T13:30:00",
            )
        return result, m

    def test_unwraps_post_envelope(self):
        """Wrapped {"post": {...}} -> caller reads _id/status at top level."""
        result, _ = self._call(
            {"post": {"_id": "6a2f16a2763536ce39ada097", "status": "scheduled",
                      "platforms": [{"platform": "twitter"}]}}
        )
        self.assertEqual(result.get("_id"), "6a2f16a2763536ce39ada097")
        self.assertEqual(result.get("status"), "scheduled")

    def test_passthrough_when_not_wrapped(self):
        """A future/flat shape (post fields at top level) is returned as-is."""
        result, _ = self._call(
            {"_id": "6a2f16a48ba53a36d102b497", "status": "published"}
        )
        self.assertEqual(result.get("_id"), "6a2f16a48ba53a36d102b497")
        self.assertEqual(result.get("status"), "published")

    def test_posts_to_correct_endpoint(self):
        """Sanity: still POSTs to /posts with the content in the body."""
        _, m = self._call({"post": {"_id": "x", "status": "scheduled"}})
        args, kwargs = m.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "/posts")
        self.assertEqual(args[2]["content"], "hello world")


if __name__ == "__main__":
    unittest.main()
