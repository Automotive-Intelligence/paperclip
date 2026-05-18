"""Tests for services/heartbeat.

The HTTP layer is patched out — these tests verify that ping_heartbeat
handles each outcome (disabled, ok, http_err, net_err) without raising,
and that the URL fingerprint stays stable + non-reversible.
"""

import os
import unittest
from unittest.mock import patch, MagicMock

import requests

from services import heartbeat


class HeartbeatDisabledTests(unittest.TestCase):
    @patch.dict(os.environ, {"HEARTBEAT_URL": ""}, clear=False)
    def test_disabled_when_env_empty(self):
        self.assertEqual(heartbeat.ping_heartbeat(), {"outcome": "disabled"})

    @patch.dict(os.environ, {"HEARTBEAT_URL": "   "}, clear=False)
    def test_disabled_when_env_whitespace(self):
        self.assertEqual(heartbeat.ping_heartbeat(), {"outcome": "disabled"})


class HeartbeatPingTests(unittest.TestCase):
    @patch.dict(os.environ, {"HEARTBEAT_URL": "https://hc-ping.com/abc-123"}, clear=False)
    @patch("services.heartbeat.requests.get")
    def test_ok_on_2xx(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp
        result = heartbeat.ping_heartbeat()
        self.assertEqual(result["outcome"], "ok")
        self.assertEqual(result["status"], 200)
        self.assertIn("elapsed_ms", result)
        mock_get.assert_called_once()

    @patch.dict(os.environ, {"HEARTBEAT_URL": "https://hc-ping.com/abc-123"}, clear=False)
    @patch("services.heartbeat.requests.get")
    def test_http_err_on_non_2xx(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp
        result = heartbeat.ping_heartbeat()
        self.assertEqual(result["outcome"], "http_err")
        self.assertEqual(result["status"], 500)

    @patch.dict(os.environ, {"HEARTBEAT_URL": "https://hc-ping.com/abc-123"}, clear=False)
    @patch("services.heartbeat.requests.get")
    def test_net_err_swallowed(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError("boom")
        result = heartbeat.ping_heartbeat()
        self.assertEqual(result["outcome"], "net_err")
        self.assertIn("boom", result["error"])

    @patch.dict(os.environ, {"HEARTBEAT_URL": "https://hc-ping.com/abc-123"}, clear=False)
    @patch("services.heartbeat.requests.get")
    def test_extra_query_passed_through(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp
        heartbeat.ping_heartbeat(extra_query={"reason": "manual"})
        call_kwargs = mock_get.call_args[1]
        self.assertEqual(call_kwargs["params"], {"reason": "manual"})


class HeartbeatStatusTests(unittest.TestCase):
    @patch.dict(os.environ, {"HEARTBEAT_URL": ""}, clear=False)
    def test_status_when_unconfigured(self):
        s = heartbeat.heartbeat_status()
        self.assertFalse(s["configured"])
        self.assertIsNone(s["url_fingerprint"])
        self.assertEqual(s["env_var"], "HEARTBEAT_URL")

    @patch.dict(os.environ, {"HEARTBEAT_URL": "https://hc-ping.com/abc-123-secret"}, clear=False)
    def test_status_returns_fingerprint_not_url(self):
        s = heartbeat.heartbeat_status()
        self.assertTrue(s["configured"])
        self.assertIsNotNone(s["url_fingerprint"])
        # Never leaks the URL or any substring of the UUID
        self.assertNotIn("abc-123-secret", s["url_fingerprint"])
        self.assertNotIn("hc-ping", s["url_fingerprint"])
        self.assertEqual(len(s["url_fingerprint"]), 8)

    @patch.dict(os.environ, {"HEARTBEAT_URL": "https://hc-ping.com/abc-123-secret"}, clear=False)
    def test_fingerprint_stable(self):
        s1 = heartbeat.heartbeat_status()
        s2 = heartbeat.heartbeat_status()
        self.assertEqual(s1["url_fingerprint"], s2["url_fingerprint"])


if __name__ == "__main__":
    unittest.main()
