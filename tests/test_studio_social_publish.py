"""Publish path: X 280-char guard (t.co-aware), cross-brand account safety, stagger."""
from services import studio_social_publish as P

STAGGER = {
    "automotive intelligence": {"twitter": "07:15", "linkedin": "07:45",
                                "instagram": "12:00", "facebook": "13:00"},
}
CFG = {"display_name": "Automotive Intelligence", "business_key": "autointelligence",
       "zernio_profile": "Automotive Intelligence",
       "platforms": ["linkedin", "x", "facebook", "instagram"]}


def test_tco_length_counts_links_as_23():
    # "hook: " is 6 visible chars; the long URL collapses to 23 -> 6 + 23 = 29.
    assert P.tco_length("hook: " + "https://example.com/a/very/long/utm?x=1&y=2&z=3") == 6 + 23


def test_x_within_limit_true_for_long_url_short_text():
    body = "Short hook here. " + "https://automotiveintelligence.io/x?" + "u" * 400
    assert P.x_within_limit(body) is True  # the long UTM must NOT false-positive


def test_x_over_limit_flagged():
    assert P.x_within_limit("a" * 281) is False


def test_resolve_accounts_maps_platform_to_account_for_right_profile():
    profiles = [{"name": "Automotive Intelligence", "_id": "prof_avi"},
                {"name": "Calling Digital", "_id": "prof_wd"}]
    accts = [
        {"platform": "twitter", "_id": "acc_x", "profileId": {"_id": "prof_avi"}},
        {"platform": "linkedin", "_id": "acc_li", "profileId": "prof_avi"},
        {"platform": "facebook", "_id": "acc_fb_wd", "profileId": {"_id": "prof_wd"}},
    ]
    got = P.resolve_accounts(profiles, accts, "Automotive Intelligence")
    assert got == {"twitter": "acc_x", "linkedin": "acc_li"}  # WD's fb must NOT leak in


def test_resolve_accounts_empty_when_profile_absent():
    assert P.resolve_accounts([], [], "Nope") == {}


def test_week_day_offsets_three_posts_are_tue_thu_sat():
    assert P.week_day_offsets(3) == [1, 3, 5]


def test_build_jobs_skips_platform_without_account_and_stamps_stagger():
    posts = [{"key": "p1", "theme": "t", "platforms": {
        "linkedin": "hello", "x": "hi", "facebook": "hey", "instagram": "yo"},
        "image_prompt": "x"}]
    accounts = {"twitter": "acc_x", "linkedin": "acc_li"}  # no fb/ig account
    jobs, skips = P.build_jobs(CFG, posts, "2026-07-27", accounts,
                               {"p1": "https://media/x.png"}, STAGGER, "cid")
    plats = sorted(j["platform"] for j in jobs)
    assert plats == ["linkedin", "twitter"]
    assert all(j["account_id"] in ("acc_x", "acc_li") for j in jobs)
    li = next(j for j in jobs if j["platform"] == "linkedin")
    assert li["scheduled_for"] == "2026-07-28T07:45:00"      # Tue (offset 1) @ LI peak
    assert li["media_urls"] == ["https://media/x.png"]
    assert {(k, p) for (k, p, _r) in skips} == {("p1", "facebook"), ("p1", "instagram")}


def test_build_jobs_refuses_x_over_280():
    long_x = "a" * 300
    posts = [{"key": "p1", "theme": "t",
              "platforms": {"x": long_x, "linkedin": "ok"}, "image_prompt": "x"}]
    jobs, skips = P.build_jobs(CFG, posts, "2026-07-27",
                               {"twitter": "acc_x", "linkedin": "acc_li"}, {}, STAGGER, "cid")
    assert all(j["platform"] != "twitter" for j in jobs)     # X job refused
    assert any(p == "x" and "280" in r for (_k, p, r) in skips)
