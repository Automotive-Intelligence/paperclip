from unittest import mock

from services import watchdog

SITEMAP = """<urlset>
<url><loc>https://x.co/blog</loc><lastmod>2026-07-01</lastmod></url>
<url><loc>https://x.co/blog/fresh-post</loc><lastmod>2026-07-18</lastmod></url>
<url><loc>https://x.co/blog/old-post</loc><lastmod>2026-07-10</lastmod></url>
</urlset>"""


def _cfg(hours):
    return {"brands": {"b": {"sitemap_url": "https://x.co/sitemap.xml",
            "blog_max_age_hours": hours, "severity": "warn"}}}


def test_newest_blog_post_picks_latest_slug():
    assert watchdog._newest_blog_post(SITEMAP) == ("2026-07-18", "https://x.co/blog/fresh-post")


def test_no_slug_entries_returns_none():
    assert watchdog._newest_blog_post("<urlset></urlset>") is None


def test_fresh_post_within_threshold_no_anomaly():
    with mock.patch.object(watchdog, "_fetch_text", return_value=SITEMAP), \
         mock.patch.object(watchdog, "_now_utc",
             return_value=watchdog.datetime(2026, 7, 18, 20, tzinfo=watchdog.timezone.utc)), \
         mock.patch.object(watchdog, "_http_status", return_value=200):
        assert watchdog._check_blog_freshness(_cfg(96)) == []


def test_stale_post_flags():
    with mock.patch.object(watchdog, "_fetch_text", return_value=SITEMAP), \
         mock.patch.object(watchdog, "_now_utc",
             return_value=watchdog.datetime(2026, 7, 25, tzinfo=watchdog.timezone.utc)), \
         mock.patch.object(watchdog, "_http_status", return_value=200):
        out = watchdog._check_blog_freshness(_cfg(96))
        assert any(a.fingerprint == "blog-stale-b" for a in out)


def test_newest_post_404_flags_critical():
    with mock.patch.object(watchdog, "_fetch_text", return_value=SITEMAP), \
         mock.patch.object(watchdog, "_now_utc",
             return_value=watchdog.datetime(2026, 7, 18, 20, tzinfo=watchdog.timezone.utc)), \
         mock.patch.object(watchdog, "_http_status", return_value=404):
        out = watchdog._check_blog_freshness(_cfg(96))
        assert any(a.fingerprint == "blog-404-b" and a.severity == "critical" for a in out)


def test_disabled_when_hours_zero():
    with mock.patch.object(watchdog, "_fetch_text", return_value=SITEMAP):
        assert watchdog._check_blog_freshness(_cfg(0)) == []


def test_unparseable_sitemap_flags_unknown():
    with mock.patch.object(watchdog, "_fetch_text", return_value="<urlset></urlset>"):
        out = watchdog._check_blog_freshness(_cfg(96))
        assert any(a.fingerprint == "blog-freshness-unknown-b" for a in out)
