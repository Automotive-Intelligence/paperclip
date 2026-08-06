"""services/sonar_classifier.py -- Sonar's tiered engagement-inbox classifier.

Called by services/sonar_inbox.run_sweep() for every NEW comment / ad-comment /
mention across our owned social accounts. Public API: classify(item) -> dict.
This is PRODUCTION code that can trigger a PUBLIC reply in a brand's name, so it is
built FAIL-CLOSED: when in ANY doubt it escalates to a human, and it NEVER raises
(any exception is caught and returned as an escalation).

item shape (from sonar_inbox.pull_new):
    {"id","kind"("comment"|"ad_comment"|"mention"),"account"(username),
     "platform","text","url","ts"}

return:
    {"tier": "auto"|"lead"|"escalate", "reason": str, "draft"?: str}

Tier semantics (per the monitor's run_sweep):
    escalate -> the MONITOR emails Michael (default; use for any doubt).
    lead     -> a clear prospect. THIS module owns the CRO route (an internal
                Resend notification). If that route can't be delivered, we DOWNGRADE
                to escalate tagged "LEAD:" so the lead is NEVER silently dropped.
    auto     -> a clearly-safe reply. THIS module would own the gated public send.

WHY WE NEVER RETURN "auto" TODAY (verified 2026-08-05):
    A public reply via Zernio requires account_id + post_id (comments) or
    account_id + media_id (mentions) -- see the zernio inbox reply API. The `item`
    the monitor hands us carries only the comment/mention `id`, the account
    USERNAME (not its id), text and a permalink; pull_new strips the raw record's
    ids. tools/zernio.py has no reply-to-comment helper to reuse either. So a safe
    send CANNOT be cleanly wired from the item shape. Per Sonar's rule ("if a safe
    send cannot be cleanly wired, DO NOT return auto -- escalate with the drafted
    reply so a human can post it; a silently-dropped auto is the worst outcome"),
    an auto-worthy item is returned as tier="escalate" with the gate-passed draft in
    `reason` and `draft`. Flip _AUTO_SEND_WIRED to True only after the send path is
    built and verified end-to-end.

INTEGRATION (reused house patterns, nothing reinvented):
    - LLM: services.studio_social_llm.llm_json (Anthropic Messages API via
      ANTHROPIC_API_KEY -- the only LLM key in paperclip's Doppler config). The LLM
      picks the tier and drafts the reply; deterministic Python gates enforce safety
      AFTER drafting.
    - Brand voice: config/studio_social_brands.yaml, keyed account-username -> brand.
    - Lead route: an internal Resend email (same rail as lead_capture / the monitor's
      escalation email). It is a NOTIFICATION to a human, never a public action.
    - Every network effect (LLM, email) is a module seam so tests run fully offline.
"""
from __future__ import annotations

import html as _html
import logging
import os
import re
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple

import requests

from services.studio_social_llm import llm_json  # Anthropic seam; tests patch this name

logger = logging.getLogger(__name__)

# Auto-send is intentionally OFF: the item shape cannot supply the ids a public
# Zernio reply needs (see module docstring). Do NOT flip without a verified send path.
_AUTO_SEND_WIRED = False

_MAX_DRAFT_CHARS = 300
_BRANDS_YAML = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "config", "studio_social_brands.yaml")

# Brands Sonar must never touch (Book'd/Ryan + P&P + other clients). The monitor
# already excludes these; this is a defense-in-depth net if one slips through.
_EXCLUDE_TOKENS = ("bookd", "ryan", "velazquez", "paper", "purpose", "panda", "miriam")

# account-username token -> brand key. Best-effort substring match (Zernio handles
# are not enumerated in config); an unmatched handle FAILS CLOSED (auto is blocked
# because we can't prove the voice; a lead still routes with brand="unknown").
_BRAND_ALIASES: Dict[str, Tuple[str, ...]] = {
    "automotive_intelligence": ("automotiveintelligence", "autointelligence", "automotiveintel"),
    "ai_phone_guy": ("aiphoneguy", "theaiphoneguy", "aipg", "phoneguy"),
    "worship_digital": ("worshipdigital", "callingdigital", "worship"),
    "agent_empire": ("agentempire", "buildagentempire"),
}

# ---- deterministic ESCALATE pre-filters (only ever push toward escalate) ----
_RE_LINK = re.compile(r"https?://|www\.|\b\w[\w.-]*\.(?:com|io|ai|net|org|co|shop|store)\b", re.I)
_RE_PRICE = re.compile(
    r"\bpricing\b|\bprice\b|\bprices\b|how much|what'?s the cost|\bcost\b|\bcosts\b|"
    r"\bquote\b|\brates?\b|\bfees?\b|\bbudget\b|\$", re.I)
_RE_LEGAL_MED_FIN = re.compile(
    r"\blawyer\b|\blegal\b|\bsue\b|\blawsuit\b|\brefund\b|\bchargeback\b|\bmedical\b|"
    r"diagnos|prescri|\binvest(?:ment|ing)?\b|financial advice|insurance claim|hipaa|gdpr", re.I)
_RE_SPAM = re.compile(
    r"\bdm me\b|check (?:out )?my (?:profile|page|bio|agency|service|site)|follow me\b|"
    r"link in bio|promo code|giveaway|click here|send me (?:this|the)|visit my|collab\b|"
    r"sponsor(?:ship)?\b|buy followers|\bcrypto\b|\bnft\b", re.I)
_RE_ABUSE = re.compile(
    r"\bf+u+c+k|\bsh[i1]t\b|\ba+s+s+h+o+l+e|\bbitch\b|\bidiot\b|\bstupid\b|\bmoron\b|"
    r"\bscam\b|\bscammer\b|\bfraud\b|\brip[- ]?off\b|\bsucks?\b|\bgarbage\b|\btrash\b|"
    r"\bworst\b|\bliar\b|\bpathetic\b|\bshut up\b|\bhate\b|\bawful\b|\bterrible\b|"
    r"\bdisappoint", re.I)

_PREFILTERS = (
    (_RE_ABUSE, "complaint/abuse/troll language"),
    (_RE_LINK, "contains a link (competitor promo / spam / link-drop)"),
    (_RE_PRICE, "pricing request"),
    (_RE_LEGAL_MED_FIN, "legal/medical/financial question"),
    (_RE_SPAM, "spam / engagement-bait solicitation"),
)

# ---- hard gates applied to any auto draft (reject -> escalate) ----
_RE_EMDASH = re.compile(r"[—–]|--")            # em-dash, en-dash, or "--"
_RE_DIGIT = re.compile(r"\d")                            # any digit => fail closed on stats
_RE_LINK_IN_DRAFT = re.compile(r"https?://|www\.", re.I)
_HYPE_WORDS = (
    "game-changer", "game changer", "gamechanger", "unlock", "secret", "guru",
    "revolutionary", "revolutioniz", "cutting-edge", "cutting edge", "supercharge",
    "skyrocket", "next-level", "next level", "world-class", "unleash", "ninja",
    "rockstar", "disrupt", "groundbreaking", "mind-blowing", "ultimate", "10x", "10-x",
    "insane", "magic", "effortless", "limitless",
)
_PROMISE_WORDS = (
    "$", "%", "guarantee", "guaranteed", "we promise", "i promise", "promise you",
    "money-back", "money back", "refund", "discount", "free trial", "no risk",
    "risk-free", "roi", "double your", "triple your",
)


class _ClassifierError(Exception):
    """Internal; never escapes classify()."""


# --------------------------------------------------------------------------- #
# Brand mapping
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _brand_configs() -> Dict[str, Dict[str, Any]]:
    """{brand_key: cfg} from studio_social_brands.yaml. Never raises."""
    try:
        import yaml  # local import: a yaml problem must not break module import
        with open(_BRANDS_YAML, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return dict((data.get("brands") or {}))
    except Exception as e:  # noqa: BLE001
        logger.warning("[sonar-classifier] brand config load failed: %s", e)
        return {}


def _norm(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _brand_for_account(account: Optional[str]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Map a Zernio account username -> (brand_key, cfg). (None, None) if unknown."""
    acct = _norm(account)
    if not acct:
        return None, None
    best_key, best_len = None, 0
    for key, tokens in _BRAND_ALIASES.items():
        for tok in tokens:
            t = _norm(tok)
            if len(t) >= 4 and t in acct and len(t) > best_len:
                best_key, best_len = key, len(t)
    if best_key is None:
        return None, None
    return best_key, _brand_configs().get(best_key)


def _is_excluded(account: Optional[str]) -> bool:
    acct = _norm(account)
    return any(_norm(tok) in acct for tok in _EXCLUDE_TOKENS)


# --------------------------------------------------------------------------- #
# Network seams (mocked in tests)
# --------------------------------------------------------------------------- #
def _send_email(subject: str, html_body: str) -> bool:
    """Internal Resend notification (NOT a public action). Returns True on 2xx."""
    key = (os.getenv("RESEND_API_KEY") or "").strip()
    if not key:
        logger.error("[sonar-classifier] RESEND_API_KEY missing; cannot route lead")
        return False
    to_addr = (os.getenv("SONAR_LEAD_TO") or os.getenv("LEAD_ALERT_TO")
               or "michael@automotiveintelligence.io")
    frm = os.getenv("LEAD_ALERT_FROM", "AVO Leads <cmo@mail.automotiveintelligence.io>")
    try:
        r = requests.post(
            "https://api.resend.com/emails", timeout=15,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"from": frm, "to": [to_addr], "subject": subject, "html": html_body})
        if not r.ok:
            logger.warning("[sonar-classifier] lead email %s: %s", r.status_code, r.text[:160])
        return bool(r.ok)
    except requests.RequestException:
        logger.exception("[sonar-classifier] lead email send failed")
        return False


def _route_lead_to_cro(item: Dict[str, Any], brand_key: Optional[str], intent: str) -> bool:
    """Notify CRO/Michael of a buying-intent comment. Human engages on-platform (we
    have no contact record for a social commenter, so this is a human hand-off, not a
    CRM contact write). Returns True only if the notification was delivered."""
    e = _html.escape
    acct = e(str(item.get("account") or "?"))
    plat = e(str(item.get("platform") or "?"))
    kind = e(str(item.get("kind") or "comment"))
    text = e(str(item.get("text") or "")[:500])
    url = str(item.get("url") or "")
    subject = f"[Sonar][LEAD] buying-intent {item.get('kind') or 'comment'} on @{item.get('account')} ({item.get('platform')})"
    html_body = (
        "<p><b>Sonar flagged a buying-intent engagement item for CRO follow-up.</b> "
        "No auto-reply was sent -- engage the commenter on-platform.</p>"
        f"<ul><li><b>brand:</b> {e(brand_key or 'unknown')}</li>"
        f"<li><b>account:</b> @{acct} ({plat}, {kind})</li>"
        f"<li><b>detected intent:</b> {e(intent)}</li>"
        f"<li><b>comment:</b> {text}</li>"
        f"<li><b>link:</b> <a href='{e(url)}'>{e(url) or '(no permalink)'}</a></li></ul>")
    return _send_email(subject, html_body)


# --------------------------------------------------------------------------- #
# Gates
# --------------------------------------------------------------------------- #
def _prefilter_reason(text: str) -> Optional[str]:
    """First deterministic escalate-reason a comment trips, else None."""
    for rx, reason in _PREFILTERS:
        if rx.search(text):
            return reason
    return None


def _gate_draft(draft: str) -> Optional[str]:
    """Return the first gate a draft FAILS (=> escalate), or None if it passes all.
    Deterministic and fail-closed; runs AFTER the LLM drafts."""
    d = (draft or "").strip()
    if not d:
        return "empty draft"
    if len(d) > _MAX_DRAFT_CHARS:
        return f"over {_MAX_DRAFT_CHARS} chars ({len(d)})"
    if _RE_EMDASH.search(d):
        return "contains an em-dash / en-dash"
    if _RE_DIGIT.search(d):
        return "contains a number/statistic (unverifiable -> fail closed)"
    if _RE_LINK_IN_DRAFT.search(d):
        return "contains a link"
    low = d.lower()
    for w in _HYPE_WORDS:
        if w in low:
            return f"contains hype word: {w!r}"
    for w in _PROMISE_WORDS:
        if w in low:
            return f"contains pricing/promise/guarantee marker: {w!r}"
    return None


# --------------------------------------------------------------------------- #
# LLM classification + draft
# --------------------------------------------------------------------------- #
_SYSTEM = """You are Sonar, the social engagement triage for a set of B2B/SMB brands.
You read ONE inbound public comment or mention on one of OUR OWNED brand accounts and
decide how it should be handled. You are conservative and fail-closed: when in ANY
doubt, choose "escalate".

Choose exactly one tier:
- "escalate": the DEFAULT. Use for complaints or any negative sentiment; trolls,
  abuse, or profanity; competitor mentions or competitor self-promo/link-drops;
  pricing requests; legal, medical, or financial questions; spam or engagement-bait;
  anything ambiguous or that needs a human. If unsure, escalate.
- "lead": CLEAR buying intent from a prospect -- asking how to get the service, asking
  for a demo, saying "I'm interested", describing their own need and wanting help, or
  asking to be contacted. Do NOT sell. (A bare price question is NOT a lead -> escalate.)
- "auto": ONLY a clearly-safe thank-you, a positive/agreement comment, a follow-back,
  or a simple FAQ answerable from already-published brand copy. Nothing else.

If (and only if) tier is "auto", write a short reply in the brand's voice below. The
draft MUST: be under 300 characters; contain NO em-dash or en-dash; NO hype words; NO
numbers, statistics, or percentages; NO pricing, promises, or guarantees; NO links;
and match the brand voice. If you cannot write a draft that clean, choose "escalate".

Respond with ONE JSON object only:
{"tier":"escalate|lead|auto","reason":"<=120 chars why","draft":"<auto reply or empty>"}"""


def _llm_classify(item: Dict[str, Any], brand_key: Optional[str],
                  brand_cfg: Optional[Dict[str, Any]]) -> Dict[str, str]:
    voice = ""
    if brand_cfg:
        voice = f"{brand_cfg.get('display_name', brand_key)}: {brand_cfg.get('voice', '')}. " \
                f"{brand_cfg.get('themes_note', '')}"
    user = (
        f"BRAND: {brand_key or 'unknown'}\n"
        f"BRAND VOICE: {voice or '(unknown -- do NOT auto-reply; escalate instead)'}\n"
        f"PLATFORM: {item.get('platform')}\n"
        f"ITEM TYPE: {item.get('kind')}\n"
        f"COMMENT/MENTION TEXT:\n{(item.get('text') or '')[:1000]}\n"
    )
    obj = llm_json(_SYSTEM, user, retries=2) or {}
    tier = str(obj.get("tier") or "escalate").strip().lower()
    if tier not in ("auto", "lead", "escalate"):
        tier = "escalate"
    return {
        "tier": tier,
        "reason": str(obj.get("reason") or "").strip()[:200],
        "draft": str(obj.get("draft") or "").strip(),
    }


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def classify(item: Dict[str, Any]) -> Dict[str, Any]:
    """Triage one engagement item. NEVER raises; any error => escalate (fail closed)."""
    try:
        if not isinstance(item, dict):
            return {"tier": "escalate", "reason": "invalid item (not a dict)"}
        account = item.get("account")
        text = (item.get("text") or "").strip()

        if _is_excluded(account):
            return {"tier": "escalate",
                    "reason": f"account @{account} is out of Sonar scope (excluded brand)"}
        if not text:
            return {"tier": "escalate", "reason": "empty comment text -> human review"}

        # 1) cheap deterministic escalate net (also saves an LLM call), fail-closed.
        pre = _prefilter_reason(text)
        if pre:
            return {"tier": "escalate", "reason": pre}

        # 2) resolve brand voice; unknown handle can still be a lead but never an auto.
        brand_key, brand_cfg = _brand_for_account(account)

        # 3) LLM decides tier + (for auto) drafts the reply.
        res = _llm_classify(item, brand_key, brand_cfg)
        tier, reason, draft = res["tier"], res["reason"], res["draft"]

        # 4) LEAD -> route to CRO; if the route can't deliver, downgrade to a tagged
        #    escalation so the lead is never silently dropped.
        if tier == "lead":
            intent = reason or "buying intent"
            if _route_lead_to_cro(item, brand_key, intent):
                return {"tier": "lead", "reason": f"LEAD: {intent}"}
            return {"tier": "escalate",
                    "reason": f"LEAD: {intent} (CRO route undelivered -> escalating so it is not lost)"}

        # 5) AUTO -> must have a known voice, a draft, pass ALL gates, AND a verified
        #    send path. Any failure => escalate (with the draft when we have one).
        if tier == "auto":
            if brand_cfg is None:
                return {"tier": "escalate",
                        "reason": f"auto blocked: unknown brand voice for @{account}"}
            gate = _gate_draft(draft)
            if gate:
                out = {"tier": "escalate", "reason": f"auto draft rejected by safety gate: {gate}"}
                if draft:
                    out["draft"] = draft
                return out
            if not _AUTO_SEND_WIRED:
                # gates passed, but a public Zernio reply needs ids the item lacks.
                return {"tier": "escalate",
                        "reason": ("AUTO-DRAFT ready but auto-send is not wired "
                                   "(reply needs account_id + post_id/media_id, absent "
                                   f"from item); a human should post: {draft}"),
                        "draft": draft}
            # (unreachable until _AUTO_SEND_WIRED and a verified send path exist)
            return {"tier": "auto", "reason": reason or "safe auto-reply", "draft": draft}

        # 6) escalate / anything else.
        return {"tier": "escalate", "reason": reason or "escalate (default / any doubt)"}

    except Exception as e:  # noqa: BLE001 -- classify() must never raise.
        logger.exception("[sonar-classifier] classify() error; escalating")
        return {"tier": "escalate", "reason": f"classifier exception: {e}"}
