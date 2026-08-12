"""services/sdr_aipg_no_call_close.py -- SP5: the no-call-close motion, AIPG-first.

Michael, 2026-08-11, after processing an agency-outreach video together:
confirmed AIPG as the first build. Full product-fit reasoning is in
docs/superpowers/specs/2026-08-07-sdr-first-touch-design.md's 2026-08-11
addendum -- READ IT before changing anything here. Short version: AIPG is
already a narrow, flat-monthly SaaS product (Sophie), so a low-friction
no-call close fits it; WD's rebuild motion does not (broad marketing work).

Why this is its OWN module, not an extension of sdr_first_touch.py: AIPG has
no Twenty workspace (GHL-backed), so it needs a different opportunity store
(GHL contacts + notes, not Twenty companies/opportunities) and a different
evidence type (missed-call language mined from the business's OWN Google
reviews via Places New, not a website defect). It reuses sdr_first_touch's
proven, tested guardrail primitives directly (import, not duplicate):
_in_window, _EMDASH/_FORBIDDEN, _scrutineer (the same in-line Scrutineering
Gate), _suppressed. Sends via tools.ghl.send_email (AIPG's real, live send
path -- NOT the Postal-Gmail rail: AIPG's Postal token is needs_reauth as of
2026-08-11, verified live, so tools.brand_send is not viable for AIPG today).
Every send is recorded into the SAME brand_send_audit table SP4 uses, so the
5/day/brand cap counts across both engines consistently, one source of truth.

Sending discipline note: aipg.yaml's "never send cold from theaiphoneguy.com
primary" rule governs the BULK Instantly cold-email motion (60/day/mailbox).
This is a different category: <=5/day, each one individually gate-verified
against a real, quoted review -- the same low-volume/high-verification
argument already accepted for WD/AvI/Book'd's primary-identity sends in SP4.

The guardrail stack (all fail-closed, mirrors SP4 exactly where it applies):

  source        Places (New) Text Search per AIPG ICP segment x DFW city
                (config/brands/aipg.yaml's real segments/geo)
  evidence      a DIRECT QUOTE from a real Google review containing missed-
                call/no-answer language -- never inferred, never paraphrased;
                no matching review = no candidate, not a fabricated one
  recipient     email published on the company's OWN site (reuses
                sdr_first_touch._published_email verbatim); no site email =
                exception, never a broker address
  dedup         GHL search_contact by email; a contact tagged
                'sdr-no-call-close-touched' = already sent, skip forever (v1
                is single-touch; the 3-touch sequence is a follow-up, not
                built here yet -- see module TODO at bottom)
  suppression   services/suppression union check, same as SP4
  copy          fixed template + verified-fact slots (the quoted review
                snippet + the business name); deterministic validator proves
                the quote appears verbatim in the captured evidence, no
                digits beyond evidence, no pricing vocabulary, no em-dash
  scrutineering the SAME in-line Scrutineering Gate as SP4 (imported, not
                reimplemented) -- Tier-0 kill-switches + 0-5 scoring, scorer
                down = BLOCK
  window        Mon-Fri 08:00-17:30 CT only (imported from sdr_first_touch)
  cap           5 sends/day, shared brand_send_audit pool with SP4's "aipg"
                identity (not a separate budget)
  kill switch   SDR_FIRST_TOUCH_ENABLED=1 required (same switch as SP4 --
                one owner-controlled flag for all autonomous SDR sends)
  send          tools.ghl.send_email via AIPG's live GHL connection; audited
                into brand_send_audit for cap consistency
  receipt       a GHL contact note + tag, the durable dedup marker

Permanent holds unchanged: no pricing content ever; angry/negative replies
never answered by machine; spend untouched.

commit=False (default) is a full dry-run: sources, evaluates every gate,
writes nothing, sends nothing.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

from services.sdr_first_touch import (
    _EMDASH,
    _FORBIDDEN,
    _in_window,
    _published_email,
    _scrutineer,
    _suppressed,
)

logger = logging.getLogger(__name__)

_CT = ZoneInfo("America/Chicago")
DAILY_CAP = 5  # shared brand_send_audit pool with SP4's aipg identity
_SEAT = "sdr_aipg_no_call_close"
_TOUCHED_TAG = "sdr-no-call-close-touched"

_PLACES_URL = "https://places.googleapis.com/v1/places:searchText"

# AIPG's real ICP segments (config/brands/aipg.yaml) -> a real Places search
# phrase. dental_practice_manager/pi_law_partner are personas, not segments
# with a natural search phrase of their own -- the segments below are the
# yaml's actual `icp.segments` list.
_SEGMENT_QUERIES: Dict[str, str] = {
    "plumbing_operator": "plumber",
    "hvac_operator": "hvac contractor",
    "roofing_operator": "roofing contractor",
    "dental_practice_manager": "dental practice",
    "pi_law_partner": "personal injury law firm",
}

# DFW cities -- config/brands/aipg.yaml: center Dallas TX, 40mi radius.
DEFAULT_CITIES: List[str] = [
    "Dallas, Texas", "Fort Worth, Texas", "Arlington, Texas", "Irving, Texas",
    "Plano, Texas", "Grand Prairie, Texas", "Mesquite, Texas",
]

_FRANCHISE_MARKERS = (
    "keller williams", "coldwell banker", "re/max", "remax", "century 21",
    "berkshire hathaway homeservices", "compass real estate", "exp realty",
)

# Real, honest missed-call/no-answer language. Matched against ACTUAL review
# text only -- never inferred from a review count or rating alone.
_MISSED_CALL_LANGUAGE = re.compile(
    r"never (call(ed)? (me )?back|answer(ed)?|picked up|returned my call)|"
    r"no(t| one) (answer(ed|ing)?|call(ed)? back|pick(ed)? up)|"
    r"could(n'?t| not) (get|reach) (anyone|someone|a (person|human))|"
    r"left (a )?(voice ?mail|message)[^.!?]{0,60}(never|no one|nobody|didn'?t)|"
    r"(went|goes) (straight |right )?to voice ?mail|"
    r"hard to (reach|get (a hold of|ahold of))|"
    r"took (days|forever|hours) to (call|hear) back|"
    r"nobody (answers|picks up|called)",
    re.I,
)


def _places_key() -> str:
    key = (os.getenv("GOOGLE_PLACES_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("GOOGLE_PLACES_API_KEY not set")
    return key


def _domain_host(url: str) -> str:
    h = (url or "").strip().lower()
    h = h.split("://", 1)[-1].split("/", 1)[0]
    return h[4:] if h.startswith("www.") else h


def _is_franchise(name: str) -> bool:
    n = (name or "").strip().lower()
    return any(marker in n for marker in _FRANCHISE_MARKERS)


def search_places_with_reviews(query: str, *, limit: int = 5) -> List[dict]:
    """Real Places (New) Text Search with review text + phone. Returns
    [{name, domain, phone, reviews: [str,...]}], website-less results
    dropped (no way to verify a recipient email without a site)."""
    r = requests.post(
        _PLACES_URL, timeout=15,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": _places_key(),
            "X-Goog-FieldMask": "places.displayName,places.reviews,"
                                "places.nationalPhoneNumber,places.websiteUri",
        },
        json={"textQuery": query, "maxResultCount": limit},
    )
    r.raise_for_status()
    out = []
    for p in r.json().get("places", []):
        name = (p.get("displayName") or {}).get("text", "").strip()
        site = p.get("websiteUri") or ""
        domain = _domain_host(site)
        if not domain or _is_franchise(name):
            continue
        review_texts = [
            (rv.get("text") or {}).get("text", "").strip()
            for rv in (p.get("reviews") or [])
        ]
        out.append({
            "name": name,
            "domain": domain,
            "phone": p.get("nationalPhoneNumber") or "",
            "reviews": [t for t in review_texts if t],
        })
    return out


def find_missed_call_evidence(reviews: List[str]) -> Optional[str]:
    """The ONE honest evidence source for this motion: a direct quote from a
    real review containing missed-call/no-answer language. Returns the exact
    matched review text (never a paraphrase), or None if no review matches --
    a business with no such review is simply not a candidate."""
    for text in reviews:
        if _MISSED_CALL_LANGUAGE.search(text):
            return text
    return None


# --------------------------------------------------------------------------- copy + validation

_SUBJECT_TMPL = "about {domain}"

_BODY_TMPL = """Hi there,

I read through {name}'s reviews recently, honest reason below, and one stood out: "{quote}"

I run The AI Phone Guy. We build Sophie, an AI phone agent that answers every call instantly, day or night, so a missed call never turns into a review like that one. No charge to talk through it: if you want, I will send over exactly how Sophie would have handled that call. If you are already covering this some other way, no need to reply.

Worth a look?

If you would rather not hear from me, reply no thanks and that is the end of it.

Michael Rodriguez
The AI Phone Guy
"""

_TRIMMED_QUOTE_LIMIT = 300


def compose(name: str, domain: str, quote: str) -> Tuple[str, str]:
    subject = _SUBJECT_TMPL.format(domain=domain)
    body = _BODY_TMPL.format(name=name.strip() or "there",
                             quote=quote.strip()[:_TRIMMED_QUOTE_LIMIT])
    return subject, body


def validate(subject: str, body: str, *, name: str, domain: str, quote: str) -> Optional[str]:
    """Deterministic proof the outgoing text is template + real evidence,
    nothing else. Returns a reason string on failure, None when clean."""
    if _EMDASH in body or _EMDASH in subject:
        return "em_dash"
    exp_subject, exp_body = compose(name, domain, quote)
    if subject != exp_subject or body != exp_body:
        return "copy_drift"
    if quote.strip()[:_TRIMMED_QUOTE_LIMIT] not in body:
        return "quote_not_verbatim"
    if _FORBIDDEN.search(name or ""):
        return "forbidden_vocab_in_slot"
    return None


# --------------------------------------------------------------------------- GHL store

def _known_contact(email: str) -> Optional[dict]:
    from tools.ghl import search_contact
    return search_contact(email=email)


def _already_touched(contact: Optional[dict]) -> bool:
    if not contact:
        return False
    return _TOUCHED_TAG in (contact.get("tags") or [])


def _mark_touched(contact_id: str, quote: str) -> None:
    from tools.ghl import add_contact_note, update_contact_tags
    try:
        update_contact_tags(contact_id, [_TOUCHED_TAG])
        add_contact_note(contact_id, f"SP5 no-call-close first touch sent "
                         f"{datetime.now(_CT).date()}. Evidence quoted: \"{quote[:200]}\"")
    except Exception as e:
        logger.warning("[sp5-aipg] dedup tag/note failed: %s", e)


def _sends_today() -> int:
    from services.database import fetch_all
    rows = fetch_all(
        "SELECT COUNT(*) AS n FROM brand_send_audit "
        "WHERE from_identity = 'michael@theaiphoneguy.com' AND outcome = 'sent' "
        "AND created_at >= date_trunc('day', now() AT TIME ZONE 'America/Chicago') "
        "AT TIME ZONE 'America/Chicago'",
        ())
    row = rows[0] if rows else {}
    return int(row.get("n") if isinstance(row, dict) else row[0]) if rows else 0


def _sends_today_safe() -> int:
    try:
        return _sends_today()
    except Exception:
        return DAILY_CAP  # cannot verify -> fail closed, treat cap as reached


# --------------------------------------------------------------------------- the engine

def run_no_call_close(commit: bool = False, now: Optional[datetime] = None,
                      cities: Optional[List[str]] = None) -> dict:
    counts = {"queried": 0, "found": 0, "considered": 0, "sent": 0, "exceptions": 0}
    lines: List[str] = []
    enabled = os.getenv("SDR_FIRST_TOUCH_ENABLED", "").strip() == "1"

    def exc(who: str, reason: str) -> None:
        counts["exceptions"] += 1
        lines.append(f"- {who} EXCEPTION {reason}")

    for segment, phrase in _SEGMENT_QUERIES.items():
        for city in (cities or DEFAULT_CITIES):
            counts["queried"] += 1
            try:
                results = search_places_with_reviews(f"{phrase} in {city}")
            except Exception as e:
                exc(f"{segment}/{city}", f"places_search_failed: {type(e).__name__}: {e}")
                continue
            for r in results:
                counts["found"] += 1
                name, domain, reviews = r["name"], r["domain"], r["reviews"]
                quote = find_missed_call_evidence(reviews)
                if not quote:
                    continue  # no honest evidence -> not a candidate, not an exception
                counts["considered"] += 1
                try:
                    if not _in_window(now):
                        exc(f"{name} ({domain})", "outside_window")
                        continue
                    email = _published_email(domain)
                    if not email:
                        exc(f"{name} ({domain})", "no_verified_email")
                        continue
                    if _suppressed(email, "aipg"):
                        exc(email, "suppressed")
                        continue
                    contact = _known_contact(email)
                    if _already_touched(contact):
                        lines.append(f"- {name}: already touched, skip")
                        continue
                    subject, body = compose(name, domain, quote)
                    v = validate(subject, body, name=name, domain=domain, quote=quote)
                    if v:
                        exc(email, f"validator:{v}")
                        continue
                    blocked, why = _scrutineer(subject, body, name, domain, "missed_call_review")
                    if blocked:
                        exc(email, f"scrutineering_block:{why[:80]}")
                        continue
                    if not commit:
                        lines.append(f"- WOULD SEND to {email} ({name}): \"{quote[:80]}\"")
                        continue
                    if not enabled:
                        exc(email, "kill_switch_off (SDR_FIRST_TOUCH_ENABLED != 1)")
                        continue
                    if _sends_today_safe() >= DAILY_CAP:
                        exc(email, "cap_reached")
                        continue
                    from services import brand_send_audit
                    from tools.ghl import create_contact, send_email
                    cid = (contact or {}).get("id")
                    if not cid:
                        created = create_contact(
                            business_name=name, city=city, business_type=segment,
                            email_hook="sdr-no-call-close", reason="missed_call_review",
                            source_agent="sdr_aipg_no_call_close", email=email,
                            phone=r.get("phone") or None, website=f"https://{domain}",
                            business_key="aiphoneguy",
                            verified_fact=quote[:250],
                        )
                        cid = created.get("id")
                    try:
                        send_email(contact_id=cid, subject=subject, body=body,
                                  from_email="michael@theaiphoneguy.com")
                        outcome = "sent"
                    except Exception as se:
                        outcome = f"error:{type(se).__name__}"
                    brand_send_audit.record(
                        seat=_SEAT, from_identity="michael@theaiphoneguy.com",
                        to_addr=email, subject=subject, attachment=None,
                        authorized=enabled, outcome=outcome,
                        detail={"quote": quote[:200], "ghl_contact_id": cid})
                    if outcome == "sent":
                        counts["sent"] += 1
                        lines.append(f"- SENT to {email} ({name}): \"{quote[:80]}\"")
                        _mark_touched(cid, quote)
                    else:
                        exc(email, outcome)
                except Exception as e:
                    exc(f"{name} ({domain})", f"{type(e).__name__}: {e}")
    return {**counts, "digest": "\n".join(lines) or "(no missed-call evidence found)"}

# TODO (flagged, not built): the 3-touch follow-up sequence SP4 has for WD.
# v1 here is single-touch only. Extending this to a sequence should reuse
# SP4's exact _has_replied()-style reply-gate pattern once this is proven.
