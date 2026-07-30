"""tests/test_studio_publish_bookd.py — Book'd profile resolution in the Studio
publish path (tools/studio_publish.py).

Book'd is directly connected under our (salesdroid) Zernio key as the profile
"Bookd" (no apostrophe), id 6a668d6c6551027c8175f883. The resolver must map the
deliverable heading "Book'd" to that profile by ID, so an apostrophe/name change
on the live profile cannot break it. These tests are fully hermetic — the
profiles map is passed in directly (no network, no live Zernio call)."""
from __future__ import annotations

import unittest

BOOKD_ID = "6a668d6c6551027c8175f883"


class TestResolveProfile(unittest.TestCase):
    def _live_profiles(self):
        # {profile_name: profile_id}, exactly the shape main() builds from
        # get_zernio_profiles(). The live Book'd profile is named "Bookd".
        return {
            "Automotive Intelligence": "69c8a44fb1f41d9da07b5b4e",
            "Calling Digital": "69c8abdaaf24ea7f56b48115",
            "Bookd": BOOKD_ID,
        }

    def test_bookd_resolves_by_id_despite_name_mismatch(self):
        # Heading is "Book'd" (apostrophe), live profile is "Bookd" (none):
        # a name match would miss, but id resolution must find it.
        from tools.studio_publish import resolve_profile
        disp, pid = resolve_profile("Book'd", self._live_profiles())
        self.assertEqual(pid, BOOKD_ID)
        self.assertEqual(disp, "Bookd")  # recovers the live display name

    def test_bookd_lowercase_and_no_apostrophe_both_resolve_by_id(self):
        from tools.studio_publish import resolve_profile
        for heading in ("bookd", "book'd", "BOOK'D", "Bookd"):
            disp, pid = resolve_profile(heading, self._live_profiles())
            self.assertEqual(pid, BOOKD_ID, f"heading={heading!r}")

    def test_bookd_id_pinned_even_if_live_profile_renamed(self):
        # Simulate the live profile getting renamed to "Book'd Inc." — id must
        # still resolve because we pin by id, not name.
        profiles = {"Book'd Inc.": BOOKD_ID}
        from tools.studio_publish import resolve_profile
        disp, pid = resolve_profile("Book'd", profiles)
        self.assertEqual(pid, BOOKD_ID)
        self.assertEqual(disp, "Book'd Inc.")

    def test_bookd_falls_back_to_name_when_id_absent(self):
        # If the pinned id is not among the live profiles, fall back to a
        # case-insensitive name match against the corrected live name "Bookd".
        profiles = {"Bookd": "some-other-id-000000000000000000000000"}
        from tools.studio_publish import resolve_profile
        disp, pid = resolve_profile("Book'd", profiles)
        self.assertEqual(pid, "some-other-id-000000000000000000000000")
        self.assertEqual(disp, "Bookd")

    def test_bookd_not_connected_returns_none(self):
        # Neither the pinned id nor a matching name present -> not connected.
        profiles = {"Automotive Intelligence": "69c8a44fb1f41d9da07b5b4e"}
        from tools.studio_publish import resolve_profile
        disp, pid = resolve_profile("Book'd", profiles)
        self.assertIsNone(pid)

    def test_name_only_brand_still_resolves_by_name(self):
        # A brand with no pinned id resolves case-insensitively by name.
        from tools.studio_publish import resolve_profile
        disp, pid = resolve_profile("Automotive Intelligence", self._live_profiles())
        self.assertEqual(pid, "69c8a44fb1f41d9da07b5b4e")
        self.assertEqual(disp, "Automotive Intelligence")

    def test_wd_resolves_via_legacy_calling_digital_profile(self):
        from tools.studio_publish import resolve_profile
        disp, pid = resolve_profile("Worship Digital", self._live_profiles())
        self.assertEqual(pid, "69c8abdaaf24ea7f56b48115")
        self.assertEqual(disp, "Calling Digital")


if __name__ == "__main__":
    unittest.main()
