import datetime as dt
import pytz
from services import pp_blog_email_rail as rail

CST = pytz.timezone("America/Chicago")
NOW = CST.localize(dt.datetime(2026, 9, 22, 9, 20))  # Tue 09:20 CT

ART = {
    "id": 577926529112,
    "title": "How to Pray: 2 Simple, Biblical Guides for Your Time With God",
    "handle": "how-to-pray-2-simple-biblical-guides-for-your-time-with-god",
    "published_at": "2026-09-22T09:00:05-05:00",
    "image": {"src": "https://cdn.shopify.com/s/files/1/0712/6168/3800/articles/hero.jpg?v=1"},
    "body_html": (
        "<h1>How to Pray: 2 Simple, Biblical Guides for Your Time With God</h1>"
        "<p>Is there a right or wrong way to pray?</p>"
        "<p>Do you ever feel a little silly talking out loud to someone you can’t see?</p>"
        "<p>It’s simply an invitation to come before God — honestly, openly, and as you are.</p>"
        "<p>“Enter into His gates with thanksgiving” — Psalm 100:4</p>"
        "<p>Fourth real paragraph that should not appear because we cap at three.</p>"
    ),
}


def test_published_today_filters_by_chicago_date_and_not_future():
    arts = [
        ART,
        {**ART, "id": 1, "published_at": "2026-09-21T09:00:00-05:00"},   # yesterday
        {**ART, "id": 2, "published_at": "2026-09-22T14:00:00-05:00"},   # later today (scheduled)
        {**ART, "id": 3, "published_at": None},                          # draft
    ]
    ids = [a["id"] for a in rail.published_today(arts, NOW)]
    assert ids == [577926529112]


def test_send_time_is_publish_plus_30_or_now_plus_10():
    when = rail.send_time(ART["published_at"], NOW)
    assert when.astimezone(CST).strftime("%H:%M") == "09:30"
    late = CST.localize(dt.datetime(2026, 9, 22, 12, 12))
    when2 = rail.send_time(ART["published_at"], late)
    assert when2.astimezone(CST).strftime("%H:%M") == "12:22"


def test_opening_paragraphs_skip_title_and_quotes_and_cap_at_three():
    paras = rail.opening_paragraphs(ART["body_html"], ART["title"])
    assert len(paras) == 3
    assert paras[0] == "Is there a right or wrong way to pray?"
    assert "—" not in " ".join(paras) and "–" not in " ".join(paras)
    assert paras[2].startswith("It\u2019s simply an invitation to come before God, honestly")


def test_build_email_html_has_links_hero_unsubscribe_and_no_em_dash():
    h = rail.build_email_html(ART, "pp_blog_test")
    assert h.count("/blogs/news/how-to-pray-2-simple") == 2  # button + hero link
    assert "/products/be-transformed-guided-mind-renewal-journal?utm_source=klaviyo" in h
    assert ART["image"]["src"] in h
    assert "{% unsubscribe %}" in h
    assert "—" not in h
    assert "Fourth real paragraph" not in h


def test_build_email_html_without_hero_omits_image_block():
    h = rail.build_email_html({**ART, "image": None}, "x")
    assert "<img" in h and "articles/hero.jpg" not in h


def test_subject_preview_and_utm():
    assert rail.subject_for(ART["title"]) == ART["title"]
    assert rail.preview_for(ART) == "Is there a right or wrong way to pray?"
    assert rail.utm_for(ART) == "pp_blog_how_to_pray_2_simple_biblical_guides_for"


def test_disabled_flag_short_circuits(monkeypatch):
    monkeypatch.setenv("PP_BLOG_EMAIL_RAIL_ENABLED", "0")
    s = rail.run_hourly(NOW)
    assert s["enabled"] is False and s["checked"] == 0
