"""Tests for Postal Agent escalation: decision rules + batched send (shadow)."""

import os

from services.postal_classifier import should_escalate
from services import postal_escalation


def _meta(sender="", subject="", snippet=""):
    return {"sender": sender, "subject": subject, "snippet": snippet}


# ----- should_escalate: prospects always -----

def test_prospect_escalates():
    ok, _ = should_escalate("intent_reply", _meta("Jane <jane@acme.com>", "Re: your pitch"))
    assert ok is True
    ok, _ = should_escalate("lead_response", _meta("Bob <bob@co.com>", "Re: quick question"))
    assert ok is True


# ----- should_escalate: bot/automated senders never -----

def test_bot_security_suppressed():
    ok, reason = should_escalate(
        "security", _meta('"vercel[bot]" <notifications@github.com>', "Deploy failed")
    )
    assert ok is False
    assert "suppressed" in reason


def test_bot_billing_suppressed():
    ok, _ = should_escalate(
        "billing", _meta("Railway <team@railway.app>", "Your invoice is ready")
    )
    assert ok is False


# ----- should_escalate: money/security only on real-problem subjects -----

def test_routine_receipt_does_not_escalate():
    ok, _ = should_escalate(
        "billing", _meta("Orb <invoices@withorb.com>", "Payment received for invoice #123")
    )
    assert ok is False


def test_payment_problem_escalates():
    ok, reason = should_escalate(
        "billing", _meta("Orb <invoices@withorb.com>", "Payment failed — action required")
    )
    assert ok is True
    assert reason == "payment problem"


def test_genuine_security_escalates():
    ok, reason = should_escalate(
        "security", _meta("Okta <security@okta.com>", "Unusual activity on your account")
    )
    assert ok is True
    assert reason == "account security"


# ----- should_escalate: noise categories never -----

def test_newsletter_never_escalates():
    ok, _ = should_escalate("newsletter", _meta("News <hi@beehiiv.com>", "Weekly digest"))
    assert ok is False


# ----- send_escalations: shadow when flag off, no exceptions -----

def test_send_escalations_shadow(monkeypatch):
    monkeypatch.delenv("POSTAL_ESCALATE_ENABLED", raising=False)
    items = [
        {"account": "wd", "category": "intent_reply", "sender": "a@b.com", "subject": "Re: hi"},
        {"account": "avi", "category": "lead_response", "sender": "c@d.com", "subject": "Re: yo"},
    ]
    res = postal_escalation.send_escalations(items)
    assert res["sent"] is False
    assert res.get("shadow") is True
    assert res["escalated"] == 2


def test_send_escalations_empty():
    assert postal_escalation.send_escalations([])["escalated"] == 0


# ----- digest formatting -----

def test_sms_and_email_build():
    items = [
        {"account": "wd", "category": "intent_reply", "sender": "a@b.com", "subject": "Re: hi"},
        {"account": "avi", "category": "billing", "sender": "x@y.com", "subject": "Payment failed"},
    ]
    sms = postal_escalation.build_sms(items)
    assert "need you" in sms
    html = postal_escalation.build_email_html(items)
    assert "wd" in html and "avi" in html
