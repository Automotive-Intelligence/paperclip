"""reply_classify: the fail-closed money-metric classifier.

The whole point of this module is that an UNKNOWN reply (an out-of-office, an
odd-worded body, a reply we couldn't parse) must never be promoted to a hot lead.
It is either a clear rejection (dropped) or it is surfaced for a human to read
(`needs_review`) -- never silently counted.
"""
from unittest import mock

from services import reply_classify as R


class _FakeResp:
    def __init__(self, payload, ok=True):
        self._p, self.ok = payload, ok

    def json(self):
        return self._p


def _mock_get(bodies):
    """Return a fake requests.get. `bodies` maps a replier email to either a body
    string, or a list of body strings (multiple messages from that replier)."""
    def _get(url, headers=None, params=None, timeout=None):
        em = (params or {}).get("search", "")
        val = bodies.get(em, "")
        texts = val if isinstance(val, list) else [val]
        items = [{"from_address_email": em, "subject": "", "body": {"text": t}} for t in texts]
        return _FakeResp({"items": items})
    return _get


def _classify(bodies):
    repliers = [{"email": e} for e in bodies]
    with mock.patch.object(R.requests, "get", _mock_get(bodies)):
        return R.classify({"Authorization": "x"}, repliers)


# ---- the "Prove it" cases from the fix spec -------------------------------------

def test_ooo_traveling_is_not_counted_and_lands_in_review():
    interested, review = _classify({"a@x.com": "I am traveling and will respond when I return"})
    assert interested == 0
    assert "a@x.com" in review


def test_automatic_reply_out_of_office_is_not_counted():
    interested, review = _classify({"b@x.com": "Automatic reply: Out of office"})
    assert interested == 0
    # an autoresponder tells us nothing about human intent -> surfaced, never dropped
    assert "b@x.com" in review


def test_negative_remove_me_is_not_counted_and_not_surfaced():
    interested, review = _classify({"c@x.com": "No thanks, remove me"})
    assert interested == 0
    assert "c@x.com" not in review   # a clear "no" is decided, not "needs eyes"


def test_pricing_question_is_counted_interested():
    interested, review = _classify({"d@x.com": "Sounds interesting -- what's your pricing?"})
    assert interested == 1
    assert "d@x.com" not in review


def test_bare_thanks_is_ambiguous_not_counted_lands_in_review():
    interested, review = _classify({"e@x.com": "Thanks for reaching out."})
    assert interested == 0
    assert "e@x.com" in review


# ---- reinforcing coverage -------------------------------------------------------

def test_classify_text_labels():
    assert R.classify_text("out of office until Monday") == "autoreply"
    assert R.classify_text("I am travelling this week") == "autoreply"
    assert R.classify_text("please reach out to my colleague") == "autoreply"
    assert R.classify_text("not interested, unsubscribe") == "negative"
    assert R.classify_text("what's your pricing?") == "positive"
    assert R.classify_text("let's talk") == "positive"
    assert R.classify_text("call me at 555-1212") == "positive"
    assert R.classify_text("Thanks for reaching out") == "unknown"


def test_expanded_autoreply_phrasings_never_count_as_interested():
    for body in ["traveling, back Monday", "I'm on leave until August", "on holiday",
                 "no longer with the company", "reach out to Jane in my absence",
                 "I'll be back in the office next week", "out of the country",
                 "currently on maternity leave"]:
        interested, review = _classify({"z@x.com": body})
        assert interested == 0, body
        assert "z@x.com" in review, body


def test_a_positive_message_outranks_an_autoreply_for_the_same_replier():
    interested, review = _classify({"f@x.com": ["Out of office", "Actually, send me pricing"]})
    assert interested == 1
    assert "f@x.com" not in review


def test_fetch_failure_is_surfaced_never_dropped():
    def boom(*a, **k):
        raise RuntimeError("network down")
    with mock.patch.object(R.requests, "get", boom):
        interested, review = R.classify({}, [{"email": "g@x.com"}])
    assert interested == 0
    assert "g@x.com" in review


def test_multiple_repliers_mixed():
    interested, review = _classify({
        "buyer@x.com": "yes please send pricing",   # positive
        "ooo@x.com": "on vacation until Friday",     # autoreply -> review
        "no@x.com": "unsubscribe",                   # negative -> dropped
        "huh@x.com": "ok",                           # unknown  -> review
    })
    assert interested == 1
    assert set(review) == {"ooo@x.com", "huh@x.com"}


def test_needs_review_is_deduped_per_email():
    interested, review = _classify({"dup@x.com": ["out of office", "still away"]})
    assert interested == 0
    assert review.count("dup@x.com") == 1


# ---- 2026-08-07: the CarHop phantom + the unnamed-lead lesson --------------------

_CARHOP_NDR = (
    "This is an automated message from mail service of tknutson@carhop.com "
    "Message not delivered. Failure reason: host carhop-com.mail.protection."
    "outlook.com[52.101.50.13] said: 550 5.4.1 Recipient address rejected: "
    "Access denied"
)


def test_ndr_bounce_text_is_never_positive():
    # The exact text that wore Instantly's "Interested" label for weeks. Note it
    # contains question-free imperative fragments and the word patterns that
    # could brush POSITIVE; it must classify as bounce before anything else.
    assert R.classify_text(_CARHOP_NDR) == "bounce"


def test_bounce_only_replier_is_flagged_as_noise_not_counted():
    interested, review = _classify({"tknutson@carhop.com": [_CARHOP_NDR, _CARHOP_NDR]})
    assert interested == 0
    assert "tknutson@carhop.com (bounce)" in review


def test_can_i_help_you_is_positive_the_ricky_case():
    # The REAL lead: a Finance Director's genuine human question. This is the
    # reply that sat 24 days because the count was not named.
    assert R.classify_text("Re: still waiting on a call back Can I help you?") == "positive"


def test_classify_detailed_names_the_positive_repliers():
    bodies = {
        "rparrish@chuckhutton.com": "Can I help you?",
        "ooo@x.com": "automatic reply: out of office",
    }
    repliers = [{"email": e} for e in bodies]
    with mock.patch.object(R.requests, "get", _mock_get(bodies)):
        positives, review = R.classify_detailed({"Authorization": "x"}, repliers)
    assert positives == ["rparrish@chuckhutton.com"]
    assert "ooo@x.com" in review
