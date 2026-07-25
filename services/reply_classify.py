"""services/reply_classify.py -- the ONE reply classifier, fail-closed.

A reply is not a lead. The "interested humans" money-metric must FAIL CLOSED: a
message we cannot positively read as a buying signal is NEVER promoted to a hot
lead. It is either a clear rejection (dropped), or it is surfaced for a human to
read (`needs_review`) -- never silently counted, never silently dropped.

This module is the single source of truth for the regexes + `classify()`. Both
`services/tp_daily_engine.py` (the TP daily heartbeat) and
`services/growth_monitor_engine.py` (the outbound monitor) import from here so the
logic can never drift between the two heartbeats again.

(The separate `avo-telemetry` repo carries its own verbatim copy of this classifier
-- that one is out of scope for this change and is tracked as a follow-up.)

Precedence, per replier (strongest signal wins):
  POSITIVE  -> counted as interested.
  NEGATIVE  -> a clear "no"; dropped (not counted, not surfaced).
  otherwise -> AUTOREPLY (a machine answered) or UNKNOWN (ambiguous human) or a
               reply we could not fetch/parse -> surfaced in `needs_review` so a
               human confirms none of them is a real lead. Unknown must never
               inflate the money number, and must never go to /dev/null.
"""
from __future__ import annotations

import re
from typing import List, Tuple

import requests

IB = "https://api.instantly.ai/api/v2"

# A clear rejection. The ONLY bucket we are confident enough to drop outright.
NEGATIVE = re.compile(
    r"\bno thanks?\b|\bnot interested\b|\bunsubscribe\b|\bremove me\b|\bstop\b|"
    r"\bdo not (contact|email)\b|\bno longer (being )?used\b|\bpiss off\b|\bspam\b",
    re.I)

# A machine answered (or the mailbox is unattended). Not a lead, but it tells us
# NOTHING about the human's intent -- so it is surfaced for review, not dropped.
# Expanded with the soft out-of-office phrasings that leaked as "interested" before
# this fix (e.g. "traveling, back Monday" -> was counted as a hot lead).
AUTOREPLY = re.compile(
    r"auto[- ]?reply|out of (the )?office|automatic reply|unavailable|on vacation|"
    r"away from my desk|will not be monitored|delivery (status|has failed)|"
    r"travell?ing|out of the country|on leave|on holiday|maternity|medical leave|"
    r"reduced (hours|capacity)|limited (access|availability)|no longer (with|at)|"
    r"has left the (company|organization)|reach out to|in my absence|"
    r"back (on|in the office)",
    re.I)

# A genuine buying / engagement signal. Checked ONLY after AUTOREPLY and NEGATIVE,
# so "not interested" (NEGATIVE) can never be misread as "interested" (POSITIVE).
POSITIVE = re.compile(
    r"\binterested\b|"
    r"\blet'?s (talk|chat|connect|discuss|meet|sync|set ?up)\b|"
    r"\b(call|text|ping|email) me\b|"
    r"\bcall back\b|\bgive me a call\b|"
    r"\bsend (me )?(the |over |some )?(info|information|details|pricing|price|deck|"
    r"link|more|a quote|proposal)\b|"
    r"\bmore (info|information|details)\b|"
    r"\b(pricing|price|priced|costs?|quote|how much|rates?)\b|"
    r"\btell me more\b|"
    r"\b(book|schedule|set ?up)( a| the)? (call|meeting|demo|time|chat)\b|"
    r"\b(demo|meeting|calendly|calendar link)\b|"
    r"\bworth a (call|chat|conversation|look|convo)\b|"
    r"\b(sounds|looks) (good|great|interesting|helpful|promising)\b|"
    r"\byes\b[^.?!]{0,40}\b(please|interested|let'?s|send|call|schedule|sure|"
    r"absolutely|definitely|sign)\b|"
    r"\b(do|does|can|could|would|what|what'?s|how) (you|your|i|it|this|the|much)\b"
    r"[^.?!\n]{0,60}\?|"
    r"\b(happy|glad|open) to (chat|talk|connect|discuss|learn more|hear more)\b",
    re.I)


def message_text(m: dict) -> str:
    """Extract the human-authored text from an Instantly email object: strip HTML,
    cut the quoted reply chain, and prepend the subject. (Preserved verbatim from
    the original classifier.)"""
    body = (m.get("body") or {}).get("text") or (m.get("body") or {}).get("html") or ""
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.split(r"On .{0,60}wrote:|-----Original|From:", body)[0]
    return f"{m.get('subject','')} {body}"


def classify_text(text: str) -> str:
    """Label a single reply body. Returns one of:
    'autoreply' | 'negative' | 'positive' | 'unknown'.

    AUTOREPLY and NEGATIVE are checked FIRST (per the fail-closed contract): a
    machine/rejection reply is never allowed to reach the POSITIVE test."""
    if AUTOREPLY.search(text):
        return "autoreply"
    if NEGATIVE.search(text):
        return "negative"
    if POSITIVE.search(text):
        return "positive"
    return "unknown"


def classify(H, repliers) -> Tuple[int, List[str]]:
    """A reply is not a lead. Read the words. FAIL CLOSED.

    Returns (interested_count, needs_review_emails):
      - interested_count: only repliers with a genuine POSITIVE buying signal.
      - needs_review_emails: repliers we could NOT confidently classify as either a
        buying signal or a clear rejection (auto-replies, ambiguous wording, or a
        reply we failed to fetch/parse). These MUST be surfaced to a human -- an
        odd-worded real lead still gets eyes; nothing is dropped to /dev/null.
    """
    interested = 0
    needs_review: List[str] = []

    def _flag(email: str) -> None:
        if email and email not in needs_review:
            needs_review.append(email)

    for l in repliers:
        em = l.get("email", "")
        try:
            r = requests.get(f"{IB}/emails", headers=H,
                             params={"limit": 10, "search": em}, timeout=25)
            msgs = [m for m in (r.json().get("items", []) if r.ok else [])
                    if (m.get("from_address_email") or "").lower() == em.lower()]
        except Exception:
            # Could not fetch the reply -> we cannot tell. Surface, never drop.
            _flag(em)
            continue

        saw_positive = saw_negative = saw_other = False
        for m in msgs:
            label = classify_text(message_text(m))
            if label == "positive":
                saw_positive = True
                break                # strongest signal; stop scanning this replier
            elif label == "negative":
                saw_negative = True
            else:                    # autoreply or unknown
                saw_other = True

        if saw_positive:
            interested += 1          # the only path that inflates the money number
        elif saw_negative:
            continue                 # a clear "no" -> drop, do not surface
        else:                        # autoreply / unknown / no message retrieved
            _flag(em)                # fail-closed: a human must confirm it is not a lead

    return interested, needs_review
