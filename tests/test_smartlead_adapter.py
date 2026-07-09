"""Tests for the Smartlead cold_email adapter in intent_workflow_runner.

The Smartlead adapter is the warmed-secondary provider for WD's cold cohort. It
must mirror the Instantly adapter's interface AND enforce the identical
fail-closed compliance gate (placeholder-address guard, one-click-unsubscribe
capability, pre-send suppression filter) — WD must never be able to send without
all three, exactly like the Instantly brands.

No network: the Smartlead HTTP layer is monkeypatched; the real wd.yaml is
loaded from disk (no external calls).
"""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml

from config.brands._schema import BrandConfig, SmartleadConfig
from services import intent_workflow_runner as runner


REPO = Path(__file__).resolve().parent.parent


def _load_wd() -> BrandConfig:
    data = yaml.safe_load((REPO / "config" / "brands" / "wd.yaml").read_text(encoding="utf-8"))
    return BrandConfig(**data)


class SchemaTests(unittest.TestCase):
    def test_wd_resolves_to_smartlead_provider(self):
        wd = _load_wd()
        self.assertIsNotNone(wd.smartlead)
        self.assertIsNone(wd.instantly)
        self.assertEqual(runner._cold_provider(wd), "smartlead")

    def test_wd_cold_cohort_only_on_secondary_domains(self):
        wd = _load_wd()
        self.assertNotIn("worshipdigital.co", wd.smartlead.sending_domains)
        self.assertIn("bestworshipdigital.com", wd.smartlead.sending_domains)
        self.assertIn("worshipdigital.co", wd.smartlead.never_send_from_domains)

    def test_domain_guard_rejects_primary_in_sending(self):
        with self.assertRaises(Exception):
            SmartleadConfig(
                api_key_env="SMARTLEAD_WD_API_KEY",
                sending_domains=["worshipdigital.co"],
                never_send_from_domains=["worshipdigital.co"],
            )

    def test_cold_email_without_any_provider_block_fails(self):
        """A brand with cold_email in roster but no instantly AND no smartlead
        block must fail schema validation (fail-closed)."""
        wd_data = yaml.safe_load((REPO / "config" / "brands" / "wd.yaml").read_text(encoding="utf-8"))
        wd_data.pop("smartlead", None)
        wd_data.pop("instantly", None)
        with self.assertRaises(Exception):
            BrandConfig(**wd_data)


class MergeAndRenderTests(unittest.TestCase):
    def test_merge_tags_mapped_to_smartlead_syntax(self):
        out = runner._smartlead_merge_tags("hi {{firstName}} at {{companyName}}")
        self.assertEqual(out, "hi {{first_name}} at {{company_name}}")

    def test_body_always_carries_one_click_unsubscribe(self):
        wd = _load_wd()
        step = wd.icp_content["smb_owner_general"].steps[0]
        html = runner._render_body_smartlead(step, wd)
        self.assertIn("{{unsubscribe_url}}", html)
        self.assertIn("{{first_name}}", html)  # firstName was mapped

    def test_html_wraps_newlines_without_ampersand_mangling(self):
        # Unlike Instantly, Smartlead keeps '&' intact.
        out = runner._smartlead_html("a & b\n\nc")
        self.assertIn("&", out)
        self.assertIn("<div>a & b</div>", out)


class ComplianceGateTests(unittest.TestCase):
    """The Smartlead load path must fail closed on each compliance precondition,
    identically to the Instantly path."""

    def setUp(self):
        # Point suppression at an empty temp dir so the ledger source is reachable.
        self._tmp = tempfile.mkdtemp()
        self._env = mock.patch.dict(os.environ, {
            "SUPPRESSION_DIR": self._tmp,
            "SMARTLEAD_WD_API_KEY": "test-key",
        }, clear=False)
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def _wd_with_leadfile(self, tmpdir):
        """WD config pointed at a real, readable lead CSV so we reach the
        unsubscribe/suppression preflights rather than short-circuiting."""
        wd = _load_wd()
        csv_path = Path(tmpdir) / "leads.csv"
        csv_path.write_text("email,first_name,last_name,company\nx@example.com,X,Y,Acme\n", encoding="utf-8")
        wd.leads_dir = tmpdir
        wd.icp_content["smb_owner_general"].lead_file = "leads.csv"
        return wd

    def test_blocked_without_one_click_unsubscribe(self):
        wd = _load_wd()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("UNSUBSCRIBE_SIGNING_SECRET", None)
            os.environ.pop("PUBLIC_UNSUB_BASE_URL", None)
            rc = runner._load_leads_smartlead(wd, "cfgver", "smb_owner_general")
        self.assertEqual(rc, 3)  # fail-closed

    def test_blocked_on_placeholder_address(self):
        wd = _load_wd()
        wd.compliance_profile.physical_address = "Worship Digital, DFW, TX (update before any live send)"
        rc = runner._load_leads_smartlead(wd, "cfgver", "smb_owner_general")
        self.assertEqual(rc, 3)

    def test_suppression_filter_runs_and_drops_opt_outs(self):
        """With a valid unsubscribe config + a suppressed address in the ledger,
        the suppressed lead is dropped and only clean leads are POSTed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wd = self._wd_with_leadfile(tmpdir)
            # two leads, one on the suppression ledger
            (Path(tmpdir) / "leads.csv").write_text(
                "email,first_name,last_name,company\n"
                "keep@example.com,K,P,Acme\n"
                "drop@example.com,D,Q,Beta\n",
                encoding="utf-8",
            )
            Path(self._tmp, "wd.txt").write_text("drop@example.com\n", encoding="utf-8")

            posted = []

            def fake_req(cfg, method, path, body=None, params=None):
                if method == "GET" and path == "/campaigns":
                    return [{"id": 999, "name": wd.icp_content["smb_owner_general"].campaign_name}]
                if path.endswith("/leads"):
                    posted.append(body)
                    return {"ok": True, "upload_count": len(body["lead_list"])}
                return {"ok": True}

            with mock.patch.dict(os.environ, {
                "UNSUBSCRIBE_SIGNING_SECRET": "s3cret",
                "PUBLIC_UNSUB_BASE_URL": "https://paperclip.example.com",
            }, clear=False), mock.patch.object(runner, "_smartlead_req", side_effect=fake_req):
                rc = runner._load_leads_smartlead(wd, "cfgver", "smb_owner_general")

            self.assertEqual(rc, 0)
            self.assertEqual(len(posted), 1)
            emails = [l["email"] for l in posted[0]["lead_list"]]
            self.assertIn("keep@example.com", emails)
            self.assertNotIn("drop@example.com", emails)  # suppressed
            # every enrolled lead carries a per-recipient one-click unsubscribe URL
            for lead in posted[0]["lead_list"]:
                self.assertIn("unsubscribe_url", lead["custom_fields"])
                self.assertTrue(lead["custom_fields"]["unsubscribe_url"].startswith("https://"))


class NoOpGuardTests(unittest.TestCase):
    def test_build_no_ops_without_api_key(self):
        wd = _load_wd()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SMARTLEAD_WD_API_KEY", None)
            # Should NOT raise / sys.exit; returns 0 (clean no-op).
            rc = runner._build_smartlead(wd, "cfgver", dry_run=False)
        self.assertEqual(rc, 0)

    def test_load_leads_no_ops_without_api_key(self):
        wd = _load_wd()
        wd.icp_content["smb_owner_general"].lead_file = "nonexistent.csv"
        with mock.patch.dict(os.environ, {
            "UNSUBSCRIBE_SIGNING_SECRET": "s3cret",
            "PUBLIC_UNSUB_BASE_URL": "https://paperclip.example.com",
        }, clear=False):
            os.environ.pop("SMARTLEAD_WD_API_KEY", None)
            rc = runner._load_leads_smartlead(wd, "cfgver", "smb_owner_general")
        self.assertEqual(rc, 0)


class BuildWiringTests(unittest.TestCase):
    def test_build_creates_campaign_and_configures_paused(self):
        wd = _load_wd()
        calls = []

        def fake_req(cfg, method, path, body=None, params=None):
            calls.append((method, path))
            if path == "/campaigns/create":
                return {"id": 4242}
            if method == "GET" and path == "/campaigns":
                return []
            return {"ok": True}

        with mock.patch.dict(os.environ, {"SMARTLEAD_WD_API_KEY": "k"}, clear=False), \
                mock.patch.object(runner, "_smartlead_req", side_effect=fake_req):
            rc = runner._build_smartlead(wd, "cfgver", dry_run=False)

        self.assertEqual(rc, 0)
        paths = [p for _, p in calls]
        self.assertIn("/campaigns/create", paths)
        self.assertIn("/campaigns/4242/sequences", paths)
        self.assertIn("/campaigns/4242/schedule", paths)
        self.assertIn("/campaigns/4242/settings", paths)
        # No mailboxes configured -> attach step skipped; and NEVER a status START.
        self.assertNotIn("/campaigns/4242/email-accounts", paths)
        self.assertFalse(any("status" in p for p in paths))


if __name__ == "__main__":
    unittest.main()
