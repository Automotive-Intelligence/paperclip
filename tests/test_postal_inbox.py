"""Tests for services/postal_inbox (Postal Agent — on-demand AVO access).

tools.gmail_multi imports google-auth at module top, which isn't installed in
the unit-test sandbox. The service lazy-imports it, so we inject a stub module
to exercise the wrappers without the dependency.
"""

import base64
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

_gmail_stub = sys.modules.get("tools.gmail_multi")
if _gmail_stub is None or not isinstance(getattr(_gmail_stub, "search", None), MagicMock):
    try:
        from tools import gmail_multi as _gmail_stub  # real module when deps present
    except Exception:
        _gmail_stub = types.ModuleType("tools.gmail_multi")
        for _fn in ("search", "get_thread", "list_labels", "ensure_label",
                    "add_label", "archive", "mark_read", "get_profile"):
            setattr(_gmail_stub, _fn, MagicMock())
        sys.modules["tools.gmail_multi"] = _gmail_stub

from services import postal_inbox as pi


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


class ValidateTests(unittest.TestCase):
    def test_known_accounts_pass(self):
        for acct in ("avi", "wd", "salesdroid", "aipg", "agentempire", "bookd"):
            self.assertEqual(pi._validate(acct), acct)

    def test_case_insensitive(self):
        self.assertEqual(pi._validate("AVI"), "avi")

    def test_unknown_raises(self):
        with self.assertRaises(ValueError):
            pi._validate("gmail")
        with self.assertRaises(ValueError):
            pi._validate("")


class BodyExtractionTests(unittest.TestCase):
    def test_prefers_plain_over_html(self):
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/html", "body": {"data": _b64("<p>hi</p>")}},
                {"mimeType": "text/plain", "body": {"data": _b64("hi there")}},
            ],
        }
        self.assertEqual(pi._extract_body(payload), "hi there")

    def test_falls_back_to_html(self):
        payload = {"mimeType": "text/html", "body": {"data": _b64("<b>x</b>")}}
        self.assertEqual(pi._extract_body(payload), "<b>x</b>")

    def test_empty_when_no_body(self):
        self.assertEqual(pi._extract_body({"mimeType": "text/plain", "body": {}}), "")


class SearchTests(unittest.TestCase):
    def test_search_shapes_results(self):
        with patch.object(_gmail_stub, "search",
                          return_value=[{"id": "t1", "snippet": "hello", "historyId": "9", "extra": "x"}]) as s:
            out = pi.search("avi", "from:bob is:unread", limit=10)
        s.assert_called_once_with("avi", "from:bob is:unread", limit=10)
        self.assertEqual(out, [{"id": "t1", "snippet": "hello", "historyId": "9"}])

    def test_search_bad_account(self):
        with self.assertRaises(ValueError):
            pi.search("nope", "q")


class ReadThreadTests(unittest.TestCase):
    def test_simplifies_messages(self):
        thread = {
            "messages": [{
                "id": "m1",
                "snippet": "snip",
                "labelIds": ["INBOX", "UNREAD"],
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Bob <bob@acme.com>"},
                        {"name": "Subject", "value": "Hi"},
                        {"name": "Date", "value": "Mon, 1 Jan 2026"},
                    ],
                    "mimeType": "text/plain",
                    "body": {"data": _b64("the body")},
                },
            }]
        }
        with patch.object(_gmail_stub, "get_thread", return_value=thread) as g:
            out = pi.read_thread("wd", "t99")
        g.assert_called_once_with("wd", "t99", message_format="full")
        self.assertEqual(out["message_count"], 1)
        msg = out["messages"][0]
        self.assertEqual(msg["from"], "Bob <bob@acme.com>")
        self.assertEqual(msg["subject"], "Hi")
        self.assertEqual(msg["body"], "the body")
        self.assertEqual(msg["label_ids"], ["INBOX", "UNREAD"])

    def test_missing_thread_id(self):
        with self.assertRaises(ValueError):
            pi.read_thread("wd", "")


class ModifyTests(unittest.TestCase):
    def test_apply_label_ensures_then_adds(self):
        with patch.object(_gmail_stub, "ensure_label", return_value="L7") as ens, \
             patch.object(_gmail_stub, "add_label") as add:
            out = pi.apply_label("avi", "t1", "Postal/intent_reply")
        ens.assert_called_once_with("avi", "Postal/intent_reply")
        add.assert_called_once_with("avi", "t1", "L7")
        self.assertTrue(out["ok"])
        self.assertEqual(out["label_id"], "L7")

    def test_apply_label_requires_label(self):
        with self.assertRaises(ValueError):
            pi.apply_label("avi", "t1", "")

    def test_archive(self):
        with patch.object(_gmail_stub, "archive") as arch:
            out = pi.archive("salesdroid", "t2")
        arch.assert_called_once_with("salesdroid", "t2")
        self.assertEqual(out["action"], "archived")

    def test_mark_read(self):
        with patch.object(_gmail_stub, "mark_read") as mr:
            out = pi.mark_read("bookd", "t3")
        mr.assert_called_once_with("bookd", "t3")
        self.assertEqual(out["action"], "marked_read")


if __name__ == "__main__":
    unittest.main()
