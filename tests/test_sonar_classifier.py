"""Sonar classifier: fail-closed tiering, hard gates, and offline determinism.

The LLM (`llm_json`) and the email seam (`_send_email`) are always mocked, so no
test touches the network. Auto-send is not wired, so an auto-worthy item is expected
to come back as escalate-with-draft (never a silent drop, never a public send).
"""
from unittest import mock

from services import sonar_classifier as SC

_AVI = "automotiveintelligence"          # maps to automotive_intelligence
_AIPG = "theaiphoneguy"                   # maps to ai_phone_guy


def _item(text, account=_AVI, kind="comment", platform="instagram"):
    return {"id": "x1", "kind": kind, "account": account, "platform": platform,
            "text": text, "url": "https://example.com/c/x1", "ts": "2026-08-05T00:00:00Z"}


def _llm(tier, reason="", draft=""):
    """Patch the LLM seam to return a fixed classification (no network)."""
    return mock.patch.object(SC, "llm_json", return_value={"tier": tier, "reason": reason, "draft": draft})


# --- escalate cases ---------------------------------------------------------

def test_troll_escalates_without_llm():
    # Abuse is caught by the deterministic pre-filter; the LLM is never consulted.
    with mock.patch.object(SC, "llm_json", side_effect=AssertionError("LLM must not be called")):
        res = SC.classify(_item("this is a total scam, you idiots suck"))
    assert res["tier"] == "escalate"


def test_competitor_self_promo_escalates():
    with mock.patch.object(SC, "llm_json", side_effect=AssertionError("LLM must not be called")):
        res = SC.classify(_item("Nice. Check out my agency at https://rivalagency.com for better rates"))
    assert res["tier"] == "escalate"


def test_pricing_question_escalates():
    with mock.patch.object(SC, "llm_json", side_effect=AssertionError("LLM must not be called")):
        res = SC.classify(_item("Looks good, how much does this cost per month?"))
    assert res["tier"] == "escalate"


def test_ambiguous_comment_escalates():
    # Passes pre-filters; the LLM (mocked) is unsure -> escalate.
    with _llm("escalate", reason="ambiguous / unclear intent"):
        res = SC.classify(_item("hmm interesting, not sure about this one"))
    assert res["tier"] == "escalate"
    assert "ambiguous" in res["reason"]


def test_excluded_brand_escalates():
    res = SC.classify(_item("thanks!", account="bookdcx"))
    assert res["tier"] == "escalate"
    assert "scope" in res["reason"]


# --- lead case --------------------------------------------------------------

def test_clear_buying_intent_routes_lead_when_cro_reachable():
    with _llm("lead", reason="wants a demo for their HVAC shop"), \
         mock.patch.object(SC, "_send_email", return_value=True) as send:
        res = SC.classify(_item("This is exactly what my HVAC company needs. How do I get started?",
                                account=_AIPG))
    assert res["tier"] == "lead"
    assert res["reason"].startswith("LEAD:")
    send.assert_called_once()          # CRO notification actually attempted+delivered


def test_lead_downgrades_to_escalate_when_cro_route_fails():
    # Fail-closed: if the CRO notification can't be delivered, the lead is NOT lost.
    with _llm("lead", reason="wants a demo"), \
         mock.patch.object(SC, "_send_email", return_value=False):
        res = SC.classify(_item("I'm interested, can someone reach out?", account=_AIPG))
    assert res["tier"] == "escalate"
    assert "LEAD:" in res["reason"]


# --- auto case (send unwired -> escalate-with-draft) ------------------------

def test_genuine_thank_you_escalates_with_draft_since_send_unwired():
    assert SC._AUTO_SEND_WIRED is False    # guard: never a silent public send
    with _llm("auto", reason="positive thank-you",
              draft="Thanks so much, we really appreciate you following along!"), \
         mock.patch.object(SC, "_send_email") as send:
        res = SC.classify(_item("Love what you all are doing, keep it up!"))
    assert res["tier"] == "escalate"                     # not auto -- send isn't wired
    assert res.get("draft")                              # the clean draft is preserved
    assert "auto-send is not wired" in res["reason"]
    send.assert_not_called()                             # a thank-you is not a lead email


def test_draft_with_em_dash_downgraded_to_escalate():
    with _llm("auto", reason="thanks", draft="Thanks so much — truly appreciate you!"):
        res = SC.classify(_item("great post"))
    assert res["tier"] == "escalate"
    assert "gate" in res["reason"]
    assert "em-dash" in res["reason"]


def test_draft_with_stat_downgraded_to_escalate():
    with _llm("auto", reason="thanks", draft="Thanks! We help shops book 40% more calls."):
        res = SC.classify(_item("nice work"))
    assert res["tier"] == "escalate"
    assert "gate" in res["reason"]


def test_auto_blocked_when_brand_voice_unknown():
    # Unknown handle -> no verified voice -> auto is refused even with a clean draft.
    with _llm("auto", reason="thanks", draft="Thanks so much!"):
        res = SC.classify(_item("thanks!", account="some_unmapped_handle"))
    assert res["tier"] == "escalate"
    assert "unknown brand" in res["reason"]


# --- gate unit checks -------------------------------------------------------

def test_gate_rejects_link_and_hype_and_promise():
    assert SC._gate_draft("check https://x.com") is not None
    assert SC._gate_draft("this is a total game-changer") is not None
    assert SC._gate_draft("we guarantee results") is not None
    assert SC._gate_draft("great, thanks for the kind words!") is None


# --- exception + malformed-input paths --------------------------------------

def test_llm_exception_escalates():
    with mock.patch.object(SC, "llm_json", side_effect=RuntimeError("network down")):
        res = SC.classify(_item("appreciate you all"))
    assert res["tier"] == "escalate"
    assert "exception" in res["reason"]


def test_non_dict_item_escalates():
    assert SC.classify(None)["tier"] == "escalate"
    assert SC.classify("not a dict")["tier"] == "escalate"


def test_empty_text_escalates():
    res = SC.classify(_item("   "))
    assert res["tier"] == "escalate"
