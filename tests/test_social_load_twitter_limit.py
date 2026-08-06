import os, sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.social_load import tweet_length, load_jobs, PostJob

_FAKE = {"zernio_list": lambda: [],
         "zernio_publish": lambda **k: {"_id": "x", "status": "scheduled"},
         "buffer_list": lambda c: [], "buffer_draft": lambda *a: "[]"}
_CFG = {"wd_rename_done": True}


@pytest.fixture(autouse=True)
def _isolate_registry(monkeypatch):
    """EVERY test in this module commits through load_jobs(brand="aipg", ...),
    which routes to the zernio rail and calls append_registry(). Without this
    blanket override, that writes fake rows into the REAL
    ~/avo-telemetry/social_registry.jsonl (it happened before and had to be
    purged). Point SOCIAL_REGISTRY_PATH at a throwaway temp file for the
    duration of each test; monkeypatch restores the prior env on teardown."""
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setenv("SOCIAL_REGISTRY_PATH", os.path.join(d, "r.jsonl"))
        yield


def _job(content, platform="twitter", account_id="acct_test"):
    # account_id is required for a zernio job to schedule at all: the loader now
    # refuses account_id=None rather than letting publish_to_zernio fan the post
    # out to every connected account on the platform. These tests are about tweet
    # length, so they carry a dummy id and stay on the scheduling path.
    return PostJob(brand="aipg", platform=platform, content=content,
                   scheduled_for="2026-07-25T07:15:00", content_id="t", entry_point="studio",
                   account_id=account_id)


def test_missing_account_id_is_refused_not_fanned_out():
    """The 2026-07-19 cross-brand incident, as a test. account_id=None reaches
    publish_to_zernio as 'every connected account on this platform'."""
    res = load_jobs([_job("short and sweet", account_id=None)], commit=True,
                    rails=_FAKE, cfg=_CFG)
    assert res[0]["action"] == "error"
    assert res[0]["detail"]["reason"] == "missing_account_id"


def test_missing_account_id_can_be_allowed_deliberately():
    res = load_jobs([_job("short and sweet", account_id=None)], commit=True,
                    rails=_FAKE, cfg=_CFG, allow_all_accounts=True)
    assert res[0]["action"] == "scheduled"


def test_tweet_length_counts_each_url_as_23():
    url = "https://example.com/x?utm_campaign=aipg_t&utm_source=twitter&utm_medium=social"
    assert len(url) > 23
    assert tweet_length(f"See {url} now") == len("See ") + 23 + len(" now")


def test_over_280_twitter_is_flagged_not_scheduled():
    res = load_jobs([_job("a" * 300)], commit=True, rails=_FAKE, cfg=_CFG)
    assert res[0]["action"] == "too_long"


def test_under_280_twitter_is_scheduled():
    res = load_jobs([_job("short and sweet")], commit=True, rails=_FAKE, cfg=_CFG)
    assert res[0]["action"] == "scheduled"


def test_long_content_on_non_twitter_is_unaffected():
    res = load_jobs([_job("a" * 300, platform="linkedin")], commit=True, rails=_FAKE, cfg=_CFG)
    assert res[0]["action"] == "scheduled"
