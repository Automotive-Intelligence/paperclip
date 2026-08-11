"""Automated prospect sourcing: dedupe, dry-run, fail-closed on dedup-source failure."""
from unittest import mock

from services import sdr_source_discovery as D


def test_dedup_against_known_names_and_domains(monkeypatch):
    monkeypatch.setattr(D, "_known_names_and_domains",
                        lambda rk: {"acme homes", "already.com"})
    monkeypatch.setattr(D, "search_places", lambda q, limit=8: [
        {"name": "Acme Homes", "domain": "brandnew.com", "rating": 4.5, "review_count": 10},  # dup by name
        {"name": "New Co", "domain": "already.com", "rating": 4.0, "review_count": 5},        # dup by domain
        {"name": "Fresh Builders", "domain": "fresh.com", "rating": 4.8, "review_count": 20}, # genuinely new
    ])
    out = D.discover_new_companies("wd", commit=False, cities=["Frisco, Texas"])
    assert out["found"] >= 3
    assert "Fresh Builders" in out["digest"]
    assert "Acme Homes" not in out["digest"]
    assert "New Co" not in out["digest"]


def test_dry_run_writes_nothing(monkeypatch):
    monkeypatch.setattr(D, "_known_names_and_domains", lambda rk: set())
    monkeypatch.setattr(D, "search_places", lambda q, limit=8: [
        {"name": "Fresh Builders", "domain": "fresh.com", "rating": 4.8, "review_count": 20},
    ])
    created = []
    monkeypatch.setattr(D, "_create_company", lambda rk, n, d: created.append(n))
    out = D.discover_new_companies("wd", commit=False, cities=["Frisco, Texas"])
    assert created == []
    assert out["written"] == 0
    assert "WOULD LOAD Fresh Builders" in out["digest"]


def test_commit_writes_only_genuinely_new_companies(monkeypatch):
    monkeypatch.setattr(D, "_known_names_and_domains", lambda rk: set())
    monkeypatch.setattr(D, "search_places", lambda q, limit=8: [
        {"name": "Fresh Builders", "domain": "fresh.com", "rating": 4.8, "review_count": 20},
    ])
    created = []
    monkeypatch.setattr(D, "_create_company", lambda rk, n, d: created.append((n, d)) or "new-id-1")
    out = D.discover_new_companies("wd", commit=True, cities=["Frisco, Texas"])
    assert created == [("Fresh Builders", "fresh.com")]
    assert out["written"] == 1


def test_dedup_source_failure_aborts_rather_than_risk_duplicates(monkeypatch):
    def boom(rk):
        raise RuntimeError("twenty down")
    monkeypatch.setattr(D, "_known_names_and_domains", boom)
    called = []
    monkeypatch.setattr(D, "search_places", lambda q, limit=8: called.append(q) or [])
    out = D.discover_new_companies("wd", commit=True)
    assert called == []  # never even queries Places if dedup can't be verified
    assert out["errors"] == 1
    assert "aborted" in out["digest"]


def test_one_bad_query_does_not_kill_the_whole_run(monkeypatch):
    monkeypatch.setattr(D, "_known_names_and_domains", lambda rk: set())
    calls = {"n": 0}
    def flaky(q, limit=8):
        calls["n"] += 1
        if "Frisco" in q:
            raise RuntimeError("places 500")
        return [{"name": "Ok Co", "domain": "ok.com", "rating": 4.5, "review_count": 8}]
    monkeypatch.setattr(D, "search_places", flaky)
    out = D.discover_new_companies("wd", commit=False, cities=["Frisco, Texas", "Plano, Texas"])
    assert out["errors"] >= 1
    assert "Ok Co" in out["digest"]


def test_junk_websiteless_and_franchise_results_are_dropped(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-key")
    payload = {"places": [
        {"displayName": {"text": "No Website Co"}, "rating": 4.0, "userRatingCount": 1},
        {"displayName": {"text": "Gmail Biz"}, "websiteUri": "https://gmail.com",
         "rating": 3.0, "userRatingCount": 2},
        {"displayName": {"text": "Keller Williams Realty Allen"}, "websiteUri": "https://allenkw.com",
         "rating": 4.7, "userRatingCount": 35},
        {"displayName": {"text": "Keller Williams Allen- Kim McCarty-The McCarty Group Realty"},
         "websiteUri": "https://mccartygroup.com", "rating": 4.9, "userRatingCount": 88},
        {"displayName": {"text": "Coldwell Banker Realty Southlake"}, "websiteUri": "https://cbsouthlake.com",
         "rating": 4.5, "userRatingCount": 60},
        {"displayName": {"text": "Real Co"}, "websiteUri": "https://real-co.com",
         "rating": 4.9, "userRatingCount": 100},
    ]}
    with mock.patch.object(D.requests, "post") as m:
        m.return_value.raise_for_status = lambda: None
        m.return_value.json = lambda: payload
        results = D.search_places("test query")
    names = {r["name"] for r in results}
    assert names == {"Real Co"}


def test_is_franchise_catches_marker_anywhere_in_the_name():
    assert D._is_franchise("Keller Williams Realty Allen") is True
    assert D._is_franchise("Keller Williams Allen- Kim McCarty-The McCarty Group Realty") is True
    assert D._is_franchise("Coldwell Banker Realty Southlake") is True
    assert D._is_franchise("RE/MAX Dallas Suburbs") is True
    assert D._is_franchise("The McCarty Group") is False
    assert D._is_franchise("Jeannie Anderson Group") is False


def test_unconfigured_brand_returns_honest_message():
    out = D.discover_new_companies("avi", commit=False)
    assert out["queried"] == 0
    assert "no sourcing motion configured" in out["digest"]


def test_missing_api_key_raises_clean_error(monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    try:
        D._places_key()
        assert False, "should have raised"
    except RuntimeError as e:
        assert "GOOGLE_PLACES_API_KEY" in str(e)
