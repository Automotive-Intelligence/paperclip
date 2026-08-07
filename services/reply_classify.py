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

# A delivery failure -- the mailbox is DEAD, no human ever saw the send. Checked
# FIRST (before AUTOREPLY) because NDR wording varies wildly and one leaked all
# the way into Instantly's UI as an "Interested" thread (tknutson@carhop.com,
# 2026-08: 6 hard bounces surfaced as the campaign's hot lead while the real
# POSITIVE replier sat unworked 24 days). A bounce is machine-certain non-human:
# it is flagged into needs_review WITH a "(bounce)" tag so the daily line shows
# it as noise, and it can never reach the POSITIVE test.
BOUNCE = re.compile(
    r"automated message from (the )?mail service|message not delivered|"
    r"could not be delivered|not be delivered to|delivery to the following "
    r"recipient failed|undeliver(able|ed)|recipient address rejected|"
    r"mail delivery (failed|subsystem)|mailer-?daemon|delivery status "
    r"notification|55[04] 5\.\d|permanent error|address rejected",
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
    'bounce' | 'autoreply' | 'negative' | 'positive' | 'unknown'.

    BOUNCE, AUTOREPLY and NEGATIVE are checked FIRST (per the fail-closed
    contract): a delivery failure or machine/rejection reply is never allowed
    to reach the POSITIVE test."""
    if BOUNCE.search(text):
        return "bounce"
    if AUTOREPLY.search(text):
        return "autoreply"
    if NEGATIVE.search(text):
        return "negative"
    if POSITIVE.search(text):
        return "positive"
    return "unknown"


def classify_detailed(H, repliers) -> Tuple[List[str], List[str]]:
    """A reply is not a lead. Read the words. FAIL CLOSED.

    Returns (positive_emails, needs_review_emails):
      - positive_emails: the NAMED repliers with a genuine POSITIVE buying
        signal. Naming them is the point -- an unnamed "1 interested" cost the
        real replier (rparrish@chuckhutton.com, "Can I help you?", 2026-07-15)
        24 unworked days while a phantom bounce wore the Interested label.
      - needs_review_emails: repliers we could NOT confidently classify as either a
        buying signal or a clear rejection (auto-replies, ambiguous wording, or a
        reply we failed to fetch/parse). These MUST be surfaced to a human -- an
        odd-worded real lead still gets eyes; nothing is dropped to /dev/null.
        A replier whose only messages are delivery failures is tagged
        "<email> (bounce)" so the human eye reads it as noise, not signal.
    """
    positives: List[str] = []
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

        saw_positive = saw_negative = saw_other = saw_bounce = False
        for m in msgs:
            label = classify_text(message_text(m))
            if label == "positive":
                saw_positive = True
                break                # strongest signal; stop scanning this replier
            elif label == "negative":
                saw_negative = True
            elif label == "bounce":
                saw_bounce = True
            else:                    # autoreply or unknown
                saw_other = True

        if saw_positive:
            if em and em not in positives:
                positives.append(em)  # the only path that inflates the money number
        elif saw_negative:
            continue                 # a clear "no" -> drop, do not surface
        elif saw_bounce and not saw_other:
            _flag(f"{em} (bounce)")  # dead mailbox: surfaced as NOISE, never signal
        else:                        # autoreply / unknown / no message retrieved
            _flag(em)                # fail-closed: a human must confirm it is not a lead

    return positives, needs_review


def classify(H, repliers) -> Tuple[int, List[str]]:
    """Back-compat wrapper over classify_detailed(): (interested_count,
    needs_review_emails). growth_monitor_engine still consumes this shape."""
    positives, needs_review = classify_detailed(H, repliers)
    return len(positives), needs_review
