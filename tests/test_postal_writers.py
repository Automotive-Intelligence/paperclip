"""Tests for services/postal_writers (Postal Agent Phase 4).

The Gmail / Twenty / Slack side effects are patched out — these tests verify
the dispatcher's routing logic, the sender parser, the workspace mapping, and
the POSTAL_WRITES_ENABLED safety gate.
"""

import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# tools.gmail_multi imports google-auth at module top, which isn't installed in
# the unit-test sandbox (it is in CI / prod). The writers lazy-import it, so we
# inject a stub module here to exercise the label/archive paths without the dep.
_gmail_stub = sys.modules.get("tools.gmail_multi")
if _gmail_stub is None or not isinstance(getattr(_gmail_stub, "ensure_label", None), MagicMock):
    try:
        from tools import gmail_multi as _gmail_stub  # real module when deps present
    except Exception:
        _gmail_stub = types.ModuleType("tools.gmail_multi")
        _gmail_stub.ensure_label = MagicMock(return_value="LBL")
        _gmail_stub.add_label = MagicMock()
        _gmail_stub.archive = MagicMock()
        sys.modules["tools.gmail_multi"] = _gmail_stub

from services import postal_writers as pw


def _meta(**over):
    base = {
        "id": "thread123",
        "sender": "Jane Doe <jane@acme.com>",
        "subject": "Re: your demo",
        "snippet": "sounds good, let's talk",
        "account_label": "wd",
    }
    base.update(over)
    return base


class ParseSenderTests(unittest.TestCase):
    def test_name_and_angle_email(self):
        self.assertEqual(
            pw.parse_sender("Jane Doe <jane@acme.com>"),
            ("Jane Doe", "jane@acme.com", "acme.com"),
        )

    def test_quoted_name(self):
        name, email, domain = pw.parse_sender('"Doe, Jane" <Jane@Acme.com>')
        self.assertEqual(email, "jane@acme.com")
        self.assertEqual(domain, "acme.com")

    def test_bare_email(self):
        self.assertEqual(
            pw.parse_sender("bob@beta.io"),
            ("", "bob@beta.io", "beta.io"),
        )

    def test_empty(self):
        self.assertEqual(pw.parse_sender(""), ("", "", ""))
        self.assertEqual(pw.parse_sender("no-email-here"), ("", "", ""))


class WritesEnabledGateTests(unittest.TestCase):
    def test_default_off(self):
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("POSTAL_WRITES_ENABLED", None)
            self.assertFalse(pw.writes_enabled())

    def test_truthy_values(self):
        for val in ("true", "1", "yes", "ON", "True"):
            with patch.dict("os.environ", {"POSTAL_WRITES_ENABLED": val}):
                self.assertTrue(pw.writes_enabled(), val)

    def test_falsy_values(self):
        for val in ("false", "0", "no", "", "off"):
            with patch.dict("os.environ", {"POSTAL_WRITES_ENABLED": val}):
                self.assertFalse(pw.writes_enabled(), val)


class ExecuteDestinationsDryRunTests(unittest.TestCase):
    def test_dry_run_tags_and_no_side_effects(self):
        with patch.object(pw, "writes_enabled", return_value=False), \
             patch.object(pw, "_apply_label") as apply_label, \
             patch.object(pw, "_post_slack") as post_slack:
            completed, failed = pw.execute_destinations(
                "wd", "intent_reply",
                ["twenty:wd", "avo_chat:revenue-sales", "label_only"],
                _meta(),
            )
        self.assertEqual(
            completed,
            ["dry-run:twenty:wd", "dry-run:avo_chat:revenue-sales", "dry-run:label_only"],
        )
        self.assertEqual(failed, [])
        apply_label.assert_not_called()
        post_slack.assert_not_called()


class ExecuteDestinationsLiveTests(unittest.TestCase):
    def setUp(self):
        self.enabled = patch.object(pw, "writes_enabled", return_value=True)
        self.enabled.start()
        self.addCleanup(self.enabled.stop)

    def test_label_only_applies_label(self):
        with patch.object(_gmail_stub, "ensure_label", return_value="LBL_1") as ens, \
             patch.object(_gmail_stub, "add_label") as add:
            completed, failed = pw.execute_destinations(
                "wd", "newsletter", ["label_only"], _meta()
            )
        self.assertEqual(completed, ["label_only"])
        self.assertEqual(failed, [])
        ens.assert_called_once_with("wd", "Postal/newsletter")
        add.assert_called_once_with("wd", "thread123", "LBL_1")

    def test_archive_labels_then_archives(self):
        with patch.object(_gmail_stub, "ensure_label", return_value="LBL_2"), \
             patch.object(_gmail_stub, "add_label"), \
             patch.object(_gmail_stub, "archive") as arch:
            completed, _ = pw.execute_destinations(
                "salesdroid", "junk", ["archive"], _meta()
            )
        self.assertEqual(completed, ["archive"])
        arch.assert_called_once_with("salesdroid", "thread123")

    def test_slack_and_pit_wall_post(self):
        with patch.object(pw, "_post_slack") as post:
            completed, failed = pw.execute_destinations(
                "aipg", "billing", ["avo_chat:revenue-sales", "pit_wall"], _meta()
            )
        self.assertEqual(completed, ["avo_chat:revenue-sales", "pit_wall"])
        self.assertEqual(failed, [])
        channels = [c.args[0] for c in post.call_args_list]
        self.assertEqual(channels, ["revenue-sales", pw.PIT_WALL_CHANNEL])

    def test_twenty_maps_workspace_to_business(self):
        with patch("tools.twenty.push_prospects_to_twenty", return_value=[{"status": "created"}]) as push:
            completed, failed = pw.execute_destinations(
                "avi", "intent_reply", ["twenty:avi"], _meta(sender="X <x@dealer.com>")
            )
        self.assertEqual(completed, ["twenty:avi"])
        self.assertEqual(failed, [])
        _, kwargs = push.call_args
        self.assertEqual(kwargs["business_key"], "autointelligence")
        self.assertEqual(kwargs["source_agent"], "postal")

    def test_failure_is_isolated_not_raised(self):
        # First dest raises, second still runs.
        with patch.object(pw, "_write_pit_wall", side_effect=RuntimeError("slack down")), \
             patch.object(_gmail_stub, "ensure_label", return_value="L"), \
             patch.object(_gmail_stub, "add_label"):
            completed, failed = pw.execute_destinations(
                "wd", "security", ["pit_wall", "label_only"], _meta()
            )
        self.assertEqual(completed, ["label_only"])
        self.assertEqual(len(failed), 1)
        self.assertIn("pit_wall", failed[0])

    def test_unknown_destination_fails_softly(self):
        completed, failed = pw.execute_destinations(
            "wd", "other", ["bogus:dest"], _meta()
        )
        self.assertEqual(completed, [])
        self.assertEqual(len(failed), 1)
        self.assertIn("bogus:dest", failed[0])

    def test_twenty_without_email_fails(self):
        completed, failed = pw.execute_destinations(
            "wd", "intent_reply", ["twenty:wd"], _meta(sender="no-address")
        )
        self.assertEqual(completed, [])
        self.assertEqual(len(failed), 1)


if __name__ == "__main__":
    unittest.main()
