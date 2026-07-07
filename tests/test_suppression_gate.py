"""Tests for the pre-send suppression / opt-out gate (COMPLIANCE-CRITICAL).

Covers:
  - placeholder-address guard (only pp.yaml's real address passes; all stub
    brands are blocked; missing/marker/no-digit addresses are blocked)
  - the suppression filter (ledger + customers + blank-email drop)
  - FAIL-CLOSED behavior when a configured source is unreachable, and the
    explicit override
  - RFC 8058 unsubscribe token round-trip + tamper rejection + fail-closed
    when the signing secret is absent
  - record_unsubscribe writing the local ledger (which the filter then honors)

No network: the Twenty source is exercised via a stubbed reader, and the
real brand YAMLs are loaded from disk (no external calls).
"""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from services import suppression
from services import unsubscribe


def _fake_brand(brand_key="testbrand", business_key=None, address="123 Real St, Austin, TX 78748"):
    """Duck-typed BrandConfig for the guard/filter (they read attributes only)."""
    return SimpleNamespace(
        brand=brand_key,
        business_key=business_key,
        compliance_profile=SimpleNamespace(physical_address=address),
    )


class PlaceholderAddressGuardTests(unittest.TestCase):
    def test_missing_address_is_placeholder(self):
        self.assertTrue(suppression.is_placeholder_address(None))
        self.assertTrue(suppression.is_placeholder_address(""))
        self.assertTrue(suppression.is_placeholder_address("   "))

    def test_marker_addresses_blocked(self):
        self.assertTrue(suppression.is_placeholder_address(
            "Automotive Intelligence, DFW, TX\n(update with Anytime Mailbox VBA address before any live send)"
        ))
        self.assertTrue(suppression.is_placeholder_address("Worship Digital, TX (update ...)"))
        self.assertTrue(suppression.is_placeholder_address("The AI Phone Guy, DFW, TX"))

    def test_no_digit_is_placeholder(self):
        self.assertTrue(suppression.is_placeholder_address("Some Company, Austin, Texas"))

    def test_real_address_passes(self):
        self.assertFalse(suppression.is_placeholder_address(
            "Paper & Purpose, 9901 Brodie Ln #160, Austin, TX 78748"
        ))

    def test_assert_real_address_raises_on_placeholder(self):
        brand = _fake_brand(address="DFW, TX (update before any live send)")
        with self.assertRaises(suppression.PlaceholderAddressError):
            suppression.assert_real_address(brand)

    def test_assert_real_address_ok_on_real(self):
        suppression.assert_real_address(_fake_brand())  # should not raise

    def test_real_address_brands_pass_stub_brands_blocked(self):
        """Address guard on the real configs on disk. After #144 (508 Bluestem
        for AvI/AIPG/WD) and #145 (McKinney for Book'd), those brands + pp carry
        real CAN-SPAM addresses and PASS the guard. Any brand still on a stub
        (panda) must be BLOCKED (fail-closed)."""
        from config.brands._schema import BrandConfig
        import yaml

        cfg_dir = Path(__file__).resolve().parent.parent / "config" / "brands"
        results = {}
        for path in sorted(cfg_dir.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            brand = BrandConfig(**data)
            results[brand.brand] = not suppression.is_placeholder_address(
                brand.compliance_profile.physical_address
            )
        # Brands with owner-provided real addresses must pass the guard.
        for k in ("pp", "avi", "aipg", "wd", "bookd"):
            self.assertTrue(results.get(k), f"{k} should pass the address guard; got {results}")
        # Panda is still a placeholder address -> must remain blocked (fail-closed).
        self.assertFalse(results.get("panda", True), f"panda should be blocked; got {results}")


class SuppressionFilterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env_backup = dict(os.environ)
        os.environ["SUPPRESSION_DIR"] = self._tmp

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env_backup)

    def _write_ledger(self, brand_key, emails):
        Path(self._tmp, f"{brand_key}.txt").write_text("\n".join(emails) + "\n", encoding="utf-8")

    def _write_customers(self, brand_key, emails):
        lines = ["email"] + list(emails)
        Path(self._tmp, f"{brand_key}_customers.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_ledger_and_customers_are_suppressed(self):
        self._write_ledger("testbrand", ["optout@x.com", "OPTED@x.com"])
        self._write_customers("testbrand", ["customer@x.com"])
        leads = [
            {"email": "keep@x.com"},
            {"email": "optout@x.com"},       # ledger
            {"email": "opted@x.com"},        # ledger, case-normalized
            {"email": "customer@x.com"},     # customers list
            {"email": ""},                   # blank -> dropped fail-closed
        ]
        result = suppression.filter_suppressed(leads, _fake_brand("testbrand"))
        kept = {l["email"] for l in result.kept}
        self.assertEqual(kept, {"keep@x.com"})
        self.assertEqual(result.enrolled, 1)
        self.assertEqual(result.loaded, 5)
        self.assertEqual(result.suppressed_count, 4)
        self.assertFalse(result.index.degraded)

    def test_absent_sources_are_reachable_empty(self):
        # No ledger/customers files at all -> nothing suppressed, not an error.
        leads = [{"email": "a@x.com"}, {"email": "b@x.com"}]
        result = suppression.filter_suppressed(leads, _fake_brand("nofiles"))
        self.assertEqual(result.enrolled, 2)
        self.assertFalse(result.index.degraded)

    def test_is_suppressed_single(self):
        self._write_ledger("testbrand", ["no@x.com"])
        brand = _fake_brand("testbrand")
        self.assertTrue(suppression.is_suppressed("no@x.com", brand))
        self.assertFalse(suppression.is_suppressed("yes@x.com", brand))


class FailClosedTests(unittest.TestCase):
    """A CONFIGURED source that errors must block enrollment unless overridden."""

    def setUp(self):
        self._orig = suppression._read_twenty_dnc

    def tearDown(self):
        suppression._read_twenty_dnc = self._orig

    def test_unreachable_source_blocks(self):
        def boom(_bk):
            raise suppression.SuppressionSourceUnreachable("twenty down")
        suppression._read_twenty_dnc = boom
        with self.assertRaises(suppression.SuppressionSourceUnreachable):
            suppression.build_suppression_index(_fake_brand("b", business_key="callingdigital"))

    def test_override_allows_degraded_enrollment(self):
        def boom(_bk):
            raise suppression.SuppressionSourceUnreachable("twenty down")
        suppression._read_twenty_dnc = boom
        idx = suppression.build_suppression_index(
            _fake_brand("b", business_key="callingdigital"), allow_unreachable=True
        )
        self.assertTrue(idx.degraded)

    def test_filter_propagates_fail_closed(self):
        def boom(_bk):
            raise suppression.SuppressionSourceUnreachable("twenty down")
        suppression._read_twenty_dnc = boom
        with self.assertRaises(suppression.SuppressionSourceUnreachable):
            suppression.filter_suppressed([{"email": "a@x.com"}], _fake_brand("b", business_key="bookd"))


class RecordUnsubscribeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env_backup = dict(os.environ)
        os.environ["SUPPRESSION_DIR"] = self._tmp

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env_backup)

    def test_record_then_filter_drops(self):
        # Record an opt-out; a brand with no Twenty workspace -> ledger only.
        status = suppression.record_unsubscribe("bye@x.com", "nofiles")
        self.assertEqual(status, "ledger_recorded")
        result = suppression.filter_suppressed(
            [{"email": "bye@x.com"}, {"email": "hi@x.com"}], _fake_brand("nofiles")
        )
        self.assertEqual({l["email"] for l in result.kept}, {"hi@x.com"})

    def test_record_is_idempotent(self):
        suppression.record_unsubscribe("dup@x.com", "nofiles")
        suppression.record_unsubscribe("dup@x.com", "nofiles")
        text = Path(self._tmp, "nofiles.txt").read_text(encoding="utf-8")
        self.assertEqual(text.count("dup@x.com"), 1)


class UnsubscribeTokenTests(unittest.TestCase):
    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["UNSUBSCRIBE_SIGNING_SECRET"] = "test-secret-123"
        os.environ["PUBLIC_UNSUB_BASE_URL"] = "https://example.test"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env_backup)

    def test_round_trip(self):
        tok = unsubscribe.make_token("USER@X.com", "AvI")
        self.assertEqual(unsubscribe.parse_token(tok), ("user@x.com", "avi"))

    def test_url_has_no_ampersand_or_query(self):
        url = unsubscribe.unsubscribe_url("user@x.com", "avi")
        self.assertTrue(url.startswith("https://example.test/u/"))
        self.assertNotIn("&", url)
        self.assertNotIn("?", url)

    def test_headers_shape(self):
        h = unsubscribe.list_unsubscribe_headers("user@x.com", "avi")
        self.assertTrue(h["List-Unsubscribe"].startswith("<https://example.test/u/"))
        self.assertEqual(h["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")

    def test_tampered_token_rejected(self):
        tok = unsubscribe.make_token("user@x.com", "avi")
        self.assertIsNone(unsubscribe.parse_token(tok + "x"))
        self.assertIsNone(unsubscribe.parse_token(tok.replace("v1.", "v2.")))
        self.assertIsNone(unsubscribe.parse_token("garbage"))

    def test_wrong_secret_rejects(self):
        tok = unsubscribe.make_token("user@x.com", "avi")
        os.environ["UNSUBSCRIBE_SIGNING_SECRET"] = "different-secret"
        self.assertIsNone(unsubscribe.parse_token(tok))

    def test_fail_closed_without_secret(self):
        os.environ.pop("UNSUBSCRIBE_SIGNING_SECRET", None)
        self.assertFalse(unsubscribe.unsubscribe_ready())
        with self.assertRaises(unsubscribe.UnsubscribeConfigError):
            unsubscribe.make_token("user@x.com", "avi")
        # parse never raises even without a secret
        self.assertIsNone(unsubscribe.parse_token("v1.abc.def"))

    def test_fail_closed_without_base_url(self):
        os.environ.pop("PUBLIC_UNSUB_BASE_URL", None)
        self.assertFalse(unsubscribe.unsubscribe_ready())
        with self.assertRaises(unsubscribe.UnsubscribeConfigError):
            unsubscribe.unsubscribe_url("user@x.com", "avi")


if __name__ == "__main__":
    unittest.main()
