"""services/prospect_scrub.py -- re-verify claimed contact info against the
real site before calling a legacy prospect usable. Never trust the claim.
"""
from unittest import mock

from services import prospect_scrub as S


def test_phone_confirmed_ignores_formatting():
    assert S._phone_confirmed('Call <a href="tel:+18175551234">(817) 555-1234</a>', "817-555-1234")


def test_phone_not_confirmed_when_absent():
    assert not S._phone_confirmed("no phone here", "817-555-1234")


def test_email_confirmed_case_insensitive():
    assert S._email_confirmed("Reach us at Owner@Acme.COM", "owner@acme.com")


def test_no_domain_on_file_is_unverifiable():
    out = S.verify_prospect(company_name="X", domain_on_file="", claimed_phone="817-555-1234")
    assert out == {"verdict": "unverifiable", "reason": "no_domain_on_file"}


def test_no_claimed_contact_is_unverifiable():
    out = S.verify_prospect(company_name="X", domain_on_file="acme.com")
    assert out["reason"] == "no_claimed_contact_to_check"


def test_unresolvable_site_is_unverifiable(monkeypatch):
    monkeypatch.setattr(S, "resolve_primary_site", lambda d, n, city="": ("", []))
    out = S.verify_prospect(company_name="X", domain_on_file="acme.com", claimed_phone="817-555-1234")
    assert out == {"verdict": "unverifiable", "reason": "site_unresolvable"}


def test_unreachable_site_is_unverifiable(monkeypatch):
    monkeypatch.setattr(S, "resolve_primary_site", lambda d, n, city="": ("acme.com", []))
    monkeypatch.setattr(S, "_fetch_html", lambda d: "")
    out = S.verify_prospect(company_name="X", domain_on_file="acme.com", claimed_phone="817-555-1234")
    assert out["reason"] == "site_unreachable:acme.com"


def test_claimed_contact_not_on_real_site_is_unverifiable(monkeypatch):
    monkeypatch.setattr(S, "resolve_primary_site", lambda d, n, city="": ("acme.com", []))
    monkeypatch.setattr(S, "_fetch_html", lambda d: "<p>totally different content</p>")
    out = S.verify_prospect(company_name="X", domain_on_file="acme.com", claimed_phone="817-555-1234")
    assert out["reason"] == "claimed_contact_not_found_on_real_site"


def test_phone_match_verifies(monkeypatch):
    monkeypatch.setattr(S, "resolve_primary_site", lambda d, n, city="": ("acme.com", []))
    monkeypatch.setattr(S, "_fetch_html", lambda d: 'call <a href="tel:8175551234">us</a>')
    out = S.verify_prospect(company_name="X", domain_on_file="acme.com", claimed_phone="(817) 555-1234")
    assert out["verdict"] == "verified" and out["phone_confirmed"] and not out["email_confirmed"]


def test_email_match_verifies(monkeypatch):
    monkeypatch.setattr(S, "resolve_primary_site", lambda d, n, city="": ("acme.com", []))
    monkeypatch.setattr(S, "_fetch_html", lambda d: "email owner@acme.com anytime")
    out = S.verify_prospect(company_name="X", domain_on_file="acme.com", claimed_email="owner@acme.com")
    assert out["verdict"] == "verified" and out["email_confirmed"]


def test_resolve_exception_fails_closed_unverifiable(monkeypatch):
    def boom(d, n, city=""):
        raise RuntimeError("dns down")
    monkeypatch.setattr(S, "resolve_primary_site", boom)
    out = S.verify_prospect(company_name="X", domain_on_file="acme.com", claimed_phone="817-555-1234")
    assert out["verdict"] == "unverifiable" and "resolve_failed" in out["reason"]


def test_scrub_source_classifies_and_never_raises_on_one_bad_record(monkeypatch):
    import tools.twenty as T
    opps = [
        {"id": "o1", "name": "Real Co (marcus)", "companyId": "c1", "pointOfContactId": "p1"},
        {"id": "o2", "name": "Fake Co (marcus)", "companyId": "c2", "pointOfContactId": "p2"},
        {"id": "o3", "name": "No Company (marcus)", "companyId": "", "pointOfContactId": ""},
        {"id": "o4", "name": "Unrelated (someone_else)", "companyId": "c9", "pointOfContactId": ""},
    ]
    monkeypatch.setattr(T, "_workspace_config", lambda rk: ("https://x.example", "key"))
    monkeypatch.setattr(T, "_headers", lambda k: {})
    monkeypatch.setattr(T, "_iter_opportunities", lambda base, key: opps)

    def fake_company_domain(base, headers, company_id):
        return {"c1": ("Real Co", "realco.com"), "c2": ("Fake Co", "fakeco.com")}.get(company_id, ("", ""))

    def fake_person_contact(base, headers, person_id):
        return {"p1": ("817-555-1234", ""), "p2": ("817-555-9999", "")}.get(person_id, ("", ""))

    monkeypatch.setattr(S, "_company_domain", fake_company_domain)
    monkeypatch.setattr(S, "_person_contact", fake_person_contact)

    def fake_verify(*, company_name, domain_on_file, claimed_phone="", claimed_email=""):
        if company_name == "Real Co":
            return {"verdict": "verified", "real_domain": "realco.com", "phone_confirmed": True, "email_confirmed": False}
        return {"verdict": "unverifiable", "reason": "claimed_contact_not_found_on_real_site"}

    monkeypatch.setattr(S, "verify_prospect", fake_verify)

    out = S.scrub_source("callingdigital", "marcus")
    assert out["considered"] == 3  # the (someone_else) one is excluded
    assert out["verified"] == 1
    assert out["verified_list"][0]["business_name"] == "Real Co"
    assert out["verified_list"][0]["phone"] == "817-555-1234"
    assert out["unverifiable_reasons"]["no_company_linked"] == 1
    assert out["unverifiable_reasons"]["claimed_contact_not_found_on_real_site"] == 1
