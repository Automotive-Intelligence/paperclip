"""Tests for services/cmo_shipped — the real 'what shipped' signal."""

import datetime as dt

from services import cmo_shipped


# ---- title cleaning + maintenance filter ----

def test_clean_title_strips_prefix_prnum_and_verb():
    assert cmo_shipped._clean_title(
        "content(blog): add \"How to leave a marketing agency\" (#16)"
    ) == "How to leave a marketing agency"
    assert cmo_shipped._clean_title(
        "content: What 'AI-Powered' on Slide Three Means for a GM (#37)"
    ) == "What 'AI-Powered' on Slide Three Means for a GM"


def test_is_maintenance_flags_dedupe_not_content():
    assert cmo_shipped._is_maintenance("dedupe: What 'AI-Powered' ... (#39)") is True
    assert cmo_shipped._is_maintenance("chore: bump deps") is True
    assert cmo_shipped._is_maintenance("content: A real post (#5)") is False


def test_shipped_titles_dedupes_and_drops_maintenance():
    commits = [
        {"message": "dedupe: Slide Three (#39)"},
        {"message": "content: Slide Three Means for a GM (#37)"},
        {"message": "content: Slide Three Means for a GM (#33)"},  # dup title
        {"message": "content: A Second Post (#20)"},
    ]
    titles, total = cmo_shipped.shipped_titles(commits)
    assert titles == ["Slide Three Means for a GM", "A Second Post"]
    assert total == 2


# ---- social counting ----

def test_social_counts_windows_and_ignores_old():
    since = dt.datetime(2026, 7, 29, tzinfo=dt.timezone.utc)
    reg = "\n".join([
        '{"brand":"avi","scheduled_for":"2026-07-31T12:00:00+00:00"}',
        '{"brand":"avi","scheduled_for":"2026-06-01T12:00:00+00:00"}',   # too old
        '{"brand":"wd","ts":"2026-07-30T00:00:00Z"}',
        'not json',
        '',
    ])
    counts = cmo_shipped.social_counts(reg, since)
    assert counts == {"avi": 1, "wd": 1}


# ---- collect() with injected seams (no network) ----

def _fake_gh(commits_by_repo, prs_by_repo=None):
    prs_by_repo = prs_by_repo or {}

    def _gh_get(url, token):
        for repo, commits in commits_by_repo.items():
            if f"/{repo}/commits" in url:
                return commits
        for repo, prs in prs_by_repo.items():
            if f"/{repo}/pulls" in url:
                return prs
        return []
    return _gh_get


def test_collect_reports_real_posts_social_and_held():
    commits = {
        "salesdroid/automotive-intelligence": [
            {"sha": "abc1234", "commit": {"message": "content: AvI post (#37)",
                                          "committer": {"date": "2026-07-31T19:00:00Z"}}},
        ],
    }
    prs = {
        "salesdroid/worship-digital": [
            {"number": 20, "title": "WD held post", "html_url": "http://x/20",
             "head": {"ref": "blog/wd-held-2026-08-01"}},
            {"number": 21, "title": "vercel analytics", "html_url": "http://x/21",
             "head": {"ref": "vercel/install-analytics"}},  # not a blog branch
        ],
    }
    reg = '{"brand":"avi","scheduled_for":"2026-07-31T12:00:00+00:00"}'
    now = dt.datetime(2026, 8, 2, tzinfo=dt.timezone.utc)
    rows = cmo_shipped.collect(
        now=now, lookback_hours=72, gh_token="tok",
        registry_text=reg, gh_get=_fake_gh(commits, prs),
    )
    by_key = {r["key"]: r for r in rows}
    avi = by_key["autointelligence"]
    assert avi["signal_ok"] is True
    assert avi["posts"] == ["AvI post"]
    assert avi["social"] == 1
    # WD: only the blog-branch PR counts as held, analytics PR filtered out
    wd = by_key["worshipdigital"]
    assert [h["number"] for h in wd["held"]] == [20]


def test_collect_signal_unavailable_never_says_nothing_shipped():
    def _boom(url, token):
        raise RuntimeError("403")
    rows = cmo_shipped.collect(
        now=dt.datetime(2026, 8, 2, tzinfo=dt.timezone.utc),
        gh_token="tok", registry_text="", gh_get=_boom,
    )
    avi = next(r for r in rows if r["key"] == "autointelligence")
    assert avi["signal_ok"] is False
    lines = cmo_shipped.shipped_lines(avi)
    assert lines and "unavailable" in lines[0].lower()
    assert "nothing shipped" not in " ".join(lines).lower()


def test_collect_missing_token_marks_unavailable():
    rows = cmo_shipped.collect(
        now=dt.datetime(2026, 8, 2, tzinfo=dt.timezone.utc),
        gh_token="", registry_text="", gh_get=lambda u, t: [],
    )
    avi = next(r for r in rows if r["key"] == "autointelligence")
    assert avi["signal_ok"] is False
    # Book'd has no repo -> social-only, still signal_ok True (blog N/A, not failed)
    bookd = next(r for r in rows if r["key"] == "bookd")
    assert bookd["signal_ok"] is True


def test_shipped_lines_include_social_and_extra_more():
    row = {"signal_ok": True, "posts": ["A", "B", "C"], "post_count": 5, "social": 6}
    lines = cmo_shipped.shipped_lines(row)
    assert "blog: A" in lines
    assert any("+2 more" in x for x in lines)
    assert any("6 social" in x for x in lines)
