"""Tests for the send-as-brand rail (tools/brand_send.py) — gated OFF.

These tests run WITHOUT a database: services.brand_send_audit transparently
falls back to an in-process list when DATABASE_URL is unset, which is the path
exercised here. The transport is always mocked — no test ever touches the real
Gmail API.

Coverage:
  - authorized-path builds a correct send incl. attachment (transport called)
  - UNauthorized identity is HELD (transport NEVER called, never sends)
  - missing-credential (authorized, no transport) degrades to a draft
  - the audit envelope records every case (sent / held / drafted / error)
  - the gate is OFF by default (empty SEND_AUTHORIZED_MAILBOXES)
"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock

from services import brand_send_audit
from tools import brand_send

WD = "michael@worshipdigital.co"


class _FakeTransport:
    """Records whether it was called and with what."""

    def __init__(self, result=None, raises=None):
        self.calls = []
        self._result = result or {"id": "gmail-msg-123"}
        self._raises = raises

    def send_raw(self, from_identity, raw_rfc822_b64):
        self.calls.append((from_identity, raw_rfc822_b64))
        if self._raises:
            raise self._raises
        return self._result


class BrandSendTests(unittest.TestCase):
    def setUp(self):
        self._env_backup = dict(os.environ)
        # Ensure no DB, so the audit store uses its in-process fallback.
        os.environ.pop("DATABASE_URL", None)
        # Gate OFF by default for each test; individual tests opt in.
        os.environ.pop("SEND_AUTHORIZED_MAILBOXES", None)
        brand_send_audit._reset_for_tests()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env_backup)
        brand_send_audit._reset_for_tests()

    # ---- gate defaults -----------------------------------------------------

    def test_gate_is_off_by_default(self):
        self.assertEqual(brand_send.authorized_mailboxes(), set())
        self.assertFalse(brand_send.is_authorized(WD))

    def test_allowlist_parsing(self):
        os.environ["SEND_AUTHORIZED_MAILBOXES"] = f" {WD} , foo@bar.com "
        self.assertEqual(
            brand_send.authorized_mailboxes(),
            {WD, "foo@bar.com"},
        )
        self.assertTrue(brand_send.is_authorized(WD.upper()))  # case-insensitive

    # ---- UNAUTHORIZED => HELD, transport never touched ---------------------

    def test_unauthorized_is_held_and_never_sends(self):
        transport = _FakeTransport()
        result = brand_send.send_as_brand(
            to="brian@panda.example",
            subject="Your sample",
            body="Attached.",
            from_identity="wd",
            transport=transport,  # even with a live transport, gate wins
        )
        self.assertEqual(result.outcome, "held")
        self.assertFalse(result.sent)
        self.assertFalse(result.authorized)
        self.assertEqual(result.from_identity, WD)
        self.assertEqual(transport.calls, [])  # NEVER called
        # audit recorded the hold
        audit = brand_send_audit.recent()
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["outcome"], "held")
        self.assertFalse(audit[0]["authorized"])

    # ---- AUTHORIZED + transport => sends correctly, incl. attachment -------

    def test_authorized_path_sends_with_attachment(self):
        os.environ["SEND_AUTHORIZED_MAILBOXES"] = WD
        transport = _FakeTransport(result={"id": "sent-1"})

        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            fh.write("email,score\nbrian@panda.example,88\n")
            csv_path = fh.name
        self.addCleanup(lambda: os.path.exists(csv_path) and os.remove(csv_path))

        result = brand_send.send_as_brand(
            to="brian@panda.example",
            subject="Your overdue sample",
            body="Brian — here's the sample CSV.",
            from_identity="wd",
            attachment_path=csv_path,
            transport=transport,
        )
        self.assertEqual(result.outcome, "sent")
        self.assertTrue(result.sent)
        self.assertTrue(result.authorized)
        self.assertEqual(result.attachment, os.path.basename(csv_path))

        # transport called exactly once with the right identity + a raw message
        self.assertEqual(len(transport.calls), 1)
        from_id, raw_b64 = transport.calls[0]
        self.assertEqual(from_id, WD)
        import base64
        decoded = base64.urlsafe_b64decode(raw_b64).decode()
        self.assertIn(f"From: {WD}", decoded)
        self.assertIn("To: brian@panda.example", decoded)
        self.assertIn("Subject: Your overdue sample", decoded)
        self.assertIn(os.path.basename(csv_path), decoded)  # attachment filename present

        audit = brand_send_audit.recent()
        self.assertEqual(audit[0]["outcome"], "sent")
        self.assertTrue(audit[0]["authorized"])
        self.assertEqual(audit[0]["attachment"], os.path.basename(csv_path))

    # ---- AUTHORIZED but NO transport/credential => degrades to draft -------

    def test_missing_credential_degrades_to_draft(self):
        os.environ["SEND_AUTHORIZED_MAILBOXES"] = WD
        # No transport passed; get_default_transport() returns None (no cred wired).
        result = brand_send.send_as_brand(
            to="brian@panda.example",
            subject="Your sample",
            body="Attached.",
            from_identity="wd",
        )
        self.assertEqual(result.outcome, "drafted")
        self.assertFalse(result.sent)
        self.assertTrue(result.authorized)  # authorized, just no way to fire
        self.assertEqual(result.detail.get("reason"), "no_transport")
        audit = brand_send_audit.recent()
        self.assertEqual(audit[0]["outcome"], "drafted")

    # ---- transport error after authorized attempt => recorded as error -----

    def test_transport_error_is_recorded(self):
        os.environ["SEND_AUTHORIZED_MAILBOXES"] = WD
        transport = _FakeTransport(raises=RuntimeError("gmail 500"))
        result = brand_send.send_as_brand(
            to="brian@panda.example",
            subject="Your sample",
            body="Attached.",
            from_identity="wd",
            transport=transport,
        )
        self.assertEqual(result.outcome, "error")
        self.assertFalse(result.sent)
        audit = brand_send_audit.recent()
        self.assertEqual(audit[0]["outcome"], "error")

    # ---- input validation --------------------------------------------------

    def test_missing_recipient_raises(self):
        with self.assertRaises(brand_send.BrandSendError):
            brand_send.send_as_brand(to="", subject="x", body="y", from_identity="wd")

    def test_unknown_identity_raises(self):
        with self.assertRaises(brand_send.BrandSendError):
            brand_send.send_as_brand(
                to="a@b.com", subject="x", body="y", from_identity="not-an-email"
            )

    def test_missing_attachment_raises(self):
        os.environ["SEND_AUTHORIZED_MAILBOXES"] = WD
        with self.assertRaises(brand_send.BrandSendError):
            brand_send.send_as_brand(
                to="a@b.com",
                subject="x",
                body="y",
                from_identity="wd",
                attachment_path="/nonexistent/file.csv",
                transport=_FakeTransport(),
            )

    # ---- MCP surface -------------------------------------------------------

    def test_mcp_dispatch_held_when_gate_off(self):
        # Explicitly assert: with the gate OFF, the MCP path holds and does not send.
        out = brand_send.dispatch(
            "send_as_brand",
            to="brian@panda.example",
            subject="x",
            body="y",
            from_identity="wd",
        )
        self.assertFalse(out["sent"])
        self.assertFalse(out["authorized"])
        self.assertEqual(out["outcome"], "held")


if __name__ == "__main__":
    unittest.main()
