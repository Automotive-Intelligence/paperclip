"""Tests for tools/postal_inbox_tools (AVO tools over the Postal inbox API).

These wrap the six /postal/inbox/* HTTP endpoints. We patch requests.get /
requests.post to assert request shaping (url, params/body, Bearer auth) and
the backend's error contract (400/404/502 -> PostalInboxToolError).
"""

import os
import unittest
from unittest.mock import MagicMock, patch

from tools import postal_inbox_tools as t


def _resp(*, ok=True, status=200, json_body=None, text=""):
    r = MagicMock()
    r.ok = ok
    r.status_code = status
    r.text = text
    if json_body is None:
        r.json.side_effect = ValueError("no json")
    else:
        r.json.return_value = json_body
    return r


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        os.environ["PAPERCLIP_BASE_URL"] = "https://api.example.com/"
        os.environ["PAPERCLIP_API_KEY"] = "k-secret"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_missing_base_url_raises(self):
        os.environ.pop("PAPERCLIP_BASE_URL", None)
        with self.assertRaises(t.PostalInboxToolError):
            t.inbox_labels("avi")

    def test_base_url_trailing_slash_trimmed_and_bearer_sent(self):
        with patch("tools.postal_inbox_tools.requests.get",
                   return_value=_resp(json_body={"account": "avi", "labels": []})) as g:
            t.inbox_labels("avi")
        args, kwargs = g.call_args
        self.assertEqual(args[0], "https://api.example.com/postal/inbox/labels")
        self.assertEqual(kwargs["params"], {"account": "avi"})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer k-secret")

    def test_no_api_key_omits_auth_header(self):
        os.environ.pop("PAPERCLIP_API_KEY", None)
        with patch("tools.postal_inbox_tools.requests.get",
                   return_value=_resp(json_body={"labels": []})) as g:
            t.inbox_labels("avi")
        self.assertNotIn("Authorization", g.call_args.kwargs["headers"])


class RequestShapingTests(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        os.environ["PAPERCLIP_BASE_URL"] = "https://api.example.com"
        os.environ["PAPERCLIP_API_KEY"] = "k"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_search_passes_query_and_limit(self):
        body = {"account": "avi", "query": "is:unread", "threads": [{"id": "t1"}]}
        with patch("tools.postal_inbox_tools.requests.get", return_value=_resp(json_body=body)) as g:
            out = t.inbox_search("avi", "is:unread", limit=10)
        self.assertEqual(out, body)
        self.assertEqual(g.call_args.args[0], "https://api.example.com/postal/inbox/search")
        self.assertEqual(g.call_args.kwargs["params"], {"account": "avi", "q": "is:unread", "limit": 10})

    def test_thread_requires_thread_id(self):
        with self.assertRaises(t.PostalInboxToolError):
            t.inbox_thread("avi", "")

    def test_apply_label_posts_body(self):
        body = {"ok": True, "label_id": "L7"}
        with patch("tools.postal_inbox_tools.requests.post", return_value=_resp(json_body=body)) as p:
            out = t.inbox_apply_label("avi", "t1", "Postal/intent_reply")
        self.assertTrue(out["ok"])
        self.assertEqual(p.call_args.args[0], "https://api.example.com/postal/inbox/label")
        self.assertEqual(
            p.call_args.kwargs["json"],
            {"account": "avi", "thread_id": "t1", "label": "Postal/intent_reply"},
        )

    def test_apply_label_requires_label(self):
        with self.assertRaises(t.PostalInboxToolError):
            t.inbox_apply_label("avi", "t1", "")

    def test_archive_and_mark_read_post(self):
        for fn, path in ((t.inbox_archive, "archive"), (t.inbox_mark_read, "mark_read")):
            with patch("tools.postal_inbox_tools.requests.post",
                       return_value=_resp(json_body={"ok": True})) as p:
                fn("salesdroid", "t2")
            self.assertEqual(p.call_args.args[0], f"https://api.example.com/postal/inbox/{path}")
            self.assertEqual(p.call_args.kwargs["json"], {"account": "salesdroid", "thread_id": "t2"})


class ErrorContractTests(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        os.environ["PAPERCLIP_BASE_URL"] = "https://api.example.com"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_404_surfaces_status_and_detail(self):
        resp = _resp(ok=False, status=404, json_body={"detail": "no active token for 'wd'"})
        with patch("tools.postal_inbox_tools.requests.get", return_value=resp):
            with self.assertRaises(t.PostalInboxToolError) as ctx:
                t.inbox_labels("wd")
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("no active token", str(ctx.exception))

    def test_502_upstream_error(self):
        resp = _resp(ok=False, status=502, json_body={"detail": "gmail error: HttpError"})
        with patch("tools.postal_inbox_tools.requests.get", return_value=resp):
            with self.assertRaises(t.PostalInboxToolError) as ctx:
                t.inbox_search("avi", "x")
        self.assertEqual(ctx.exception.status_code, 502)

    def test_transport_error_has_no_status(self):
        import requests as _r
        with patch("tools.postal_inbox_tools.requests.get",
                   side_effect=_r.RequestException("boom")):
            with self.assertRaises(t.PostalInboxToolError) as ctx:
                t.inbox_labels("avi")
        self.assertIsNone(ctx.exception.status_code)


class RegistrationSurfaceTests(unittest.TestCase):
    def test_one_tool_per_endpoint(self):
        names = {tool["name"] for tool in t.POSTAL_INBOX_TOOLS}
        self.assertEqual(
            names,
            {
                "postal_inbox_search",
                "postal_inbox_thread",
                "postal_inbox_labels",
                "postal_inbox_apply_label",
                "postal_inbox_archive",
                "postal_inbox_mark_read",
            },
        )

    def test_every_tool_has_schema_and_callable_handler(self):
        for tool in t.POSTAL_INBOX_TOOLS:
            self.assertEqual(tool["input_schema"]["type"], "object")
            self.assertTrue(callable(tool["handler"]))
            self.assertIn("account", tool["input_schema"]["properties"])

    def test_dispatch_routes_by_name(self):
        os.environ["PAPERCLIP_BASE_URL"] = "https://api.example.com"
        try:
            with patch("tools.postal_inbox_tools.requests.get",
                       return_value=_resp(json_body={"labels": []})) as g:
                t.dispatch("postal_inbox_labels", account="avi")
            self.assertEqual(g.call_args.args[0], "https://api.example.com/postal/inbox/labels")
        finally:
            os.environ.pop("PAPERCLIP_BASE_URL", None)

    def test_dispatch_unknown_raises(self):
        with self.assertRaises(t.PostalInboxToolError):
            t.dispatch("postal_inbox_nope", account="avi")


if __name__ == "__main__":
    unittest.main()
