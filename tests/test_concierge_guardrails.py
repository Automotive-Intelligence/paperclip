"""Concierge guardrail hardening (safety): risk-term escalation + numeric scrub.

The Concierge is a LIVE, customer-facing DM/chat responder. These guards are
code-level (not prompt-only). On the keyword-matched reply path the bot must:
  1. hand off to a human AND suppress the auto-reply when the inbound message
     carries legal / medical / financial risk terms (expanded HOT regex), and
  2. never emit a $ amount / percentage / numeric $-or-percent claim even if the
     LLM system prompt is bypassed (post-generation numeric scrub).
When in doubt: suppress + hand off. A false human-handoff is safe; a wrong
number to a customer is not.

The LLM (_claude) and every transport (_send / _handoff / _zernio_send) are
mocked, so these tests are hermetic (no network, no env).
"""
from unittest import mock

import pytest

from services import concierge as c


def _payload(text, inbox_id="1", account_id=7, conv_id=45):
    """Minimal Chatwoot agent_bot 'message_created' incoming webhook."""
    return {
        "message_type": "incoming",
        "event": "message_created",
        "content": text,
        "account": {"id": account_id},
        "conversation": {
            "id": 999,
            "display_id": conv_id,
            "inbox_id": int(inbox_id),
            "meta": {"channel": "Channel::FacebookPage"},
        },
    }


def _zpayload(text, acct="69c8aef66cb7b8cf4cabaf67", conv_id="conv-1"):
    """Minimal Zernio 'message.received' incoming payload (acct -> avi)."""
    return {
        "event": "message.received",
        "message": {
            "direction": "incoming",
            "text": text,
            "conversationId": conv_id,
            "accountId": acct,
            "platform": "instagram",
        },
    }


@pytest.fixture
def live(monkeypatch):
    """Force LIVE so the send/handoff transport actually fires (default SHADOW)."""
    monkeypatch.setattr(c, "LIVE", True)


# --- item 1: legal / medical / financial risk terms -> HOT -> suppress + handoff ---
# Each message ALSO contains a real REGISTRY keyword, so WITHOUT the guard a reply
# would be generated and sent. The guard must suppress that reply and hand off.
@pytest.mark.parametrize("text", [
    "I want the DIAGNOSTIC but is there any lawsuit risk?",   # keyword avi  + legal
    "Can I get a refund first? then send the SAMPLE",         # keyword wd   + financial
    "My medical clinic needs the PLAYBOOK",                   # keyword aipg + medical
])
def test_risk_terms_force_handoff_and_suppress_reply(live, text):
    with mock.patch.object(c, "_claude", return_value="Sure, here is the link."), \
         mock.patch.object(c, "_send") as send, \
         mock.patch.object(c, "_handoff") as handoff:
        receipt = c.handle_webhook(_payload(text))
    assert receipt["hot"] is True          # HOT regex caught the risk term
    send.assert_not_called()               # auto-reply suppressed
    handoff.assert_called_once()           # routed to a human


# --- item 2: a generated reply containing a number/$/% -> suppress + handoff ---
@pytest.mark.parametrize("reply_text", [
    "It is just $99 to start.",
    "You will save 30% versus agencies.",
    "About 500 dollars a month, roughly.",
])
def test_generated_number_is_suppressed_and_handed_off(live, reply_text):
    text = "Tell me about the DIAGNOSTIC"   # clean keyword, no risk/hot term inbound
    with mock.patch.object(c, "_claude", return_value=reply_text), \
         mock.patch.object(c, "_send") as send, \
         mock.patch.object(c, "_handoff") as handoff:
        receipt = c.handle_webhook(_payload(text))
    send.assert_not_called()               # never emit a fabricated number/price
    handoff.assert_called_once()           # same handoff path as HOT
    assert receipt["hot"] is True


# --- regression: a clean keyword reply with no numbers still sends normally ---
def test_clean_reply_sends_normally(live):
    text = "Tell me about the DIAGNOSTIC"
    reply = "Happy to help. Book here: automotiveintelligence.io/diagnostic-call"
    with mock.patch.object(c, "_claude", return_value=reply), \
         mock.patch.object(c, "_send") as send, \
         mock.patch.object(c, "_handoff") as handoff:
        receipt = c.handle_webhook(_payload(text))
    assert receipt["hot"] is False
    send.assert_called_once()
    assert send.call_args.args[2] == reply     # the exact reply was sent
    handoff.assert_not_called()


# --- guard against OVER-suppression: benign minute-counts are not a price/stat ---
def test_benign_minute_count_still_sends(live):
    text = "Tell me about the DIAGNOSTIC"
    reply = "Book the free 30-minute diagnostic here: automotiveintelligence.io/diagnostic-call"
    with mock.patch.object(c, "_claude", return_value=reply), \
         mock.patch.object(c, "_send") as send, \
         mock.patch.object(c, "_handoff") as handoff:
        c.handle_webhook(_payload(text))
    send.assert_called_once()              # "30-minute" is not $/%/percent/dollars
    handoff.assert_not_called()


# --- second transport (Zernio) gets the same numeric guard ---
def test_zernio_number_is_suppressed(live):
    with mock.patch.object(c, "_claude", return_value="Only $49/mo."), \
         mock.patch.object(c, "_zernio_send") as zsend:
        receipt = c.handle_zernio(_zpayload("Tell me about the DIAGNOSTIC"))
    zsend.assert_not_called()
    assert receipt["hot"] is True


def test_zernio_clean_reply_sends(live):
    reply = "Here is the link: automotiveintelligence.io/diagnostic-call"
    with mock.patch.object(c, "_claude", return_value=reply), \
         mock.patch.object(c, "_zernio_send") as zsend:
        c.handle_zernio(_zpayload("Tell me about the DIAGNOSTIC"))
    zsend.assert_called_once()
