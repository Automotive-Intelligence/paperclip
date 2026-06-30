"""ICP-specific copy sequences for AI Phone Guy.

IRON RULES:
- NEVER mention pricing ($382/mo or any number)
- Every message is written to the OWNER — a real human
- All copy is ICP-specific
"""

import os

BOOKING_LINK = os.environ.get("BOOKING_LINK_APG", "")

SEQUENCES = {
    "plumber": [
        {
            "day": 0, "channel": "sms",
            "body": "Hey {firstName} — quick question. How many calls does {businessName} miss while you're under a sink? Most plumbers don't know the number. Sophie does. {booking_link}",
        },
        {
            "day": 2, "channel": "email",
            "subject": "The call you missed yesterday",
            "body": "A plumber in Celina was losing 2-3 jobs a week to voicemail. He didn't realize it until we showed him the data.\n\nNow every call gets answered — even when he's elbow-deep in a P-trap.\n\nWant to see how? {booking_link}",
        },
        {
            "day": 5, "channel": "sms",
            "body": "{firstName} — want to hear Sophie answer a call the way your customers would? Takes 2 minutes. {booking_link}",
        },
        {
            "day": 8, "channel": "email",
            "subject": "One missed call a day",
            "body": "Think about what one job is worth to you. Now think about how many calls go to voicemail when you're on a job.\n\nWe solve that. {booking_link}",
        },
        {
            "day": 12, "channel": "sms",
            "body": "Last one from me {firstName}. Sophie's ready when you are. {booking_link}",
        },
    ],
    "hvac": [
        {
            "day": 0, "channel": "sms",
            "body": "Hey {firstName} — when you're up on a rooftop and a new customer calls, where does that call go? {booking_link}",
        },
        {
            "day": 2, "channel": "email",
            "subject": "The HVAC owner who stopped losing calls on the roof",
            "body": "HVAC owner in McKinney. Used to climb down every time the phone rang. Half the time the caller hung up before he got there.\n\nNow Sophie handles every call. He just shows up to the jobs she books.\n\n{booking_link}",
        },
        {
            "day": 5, "channel": "sms",
            "body": "{firstName} — 2-minute live demo of Sophie on a real HVAC call. {booking_link}",
        },
        {
            "day": 8, "channel": "email",
            "subject": "Summer's coming. Every call counts.",
            "body": "DFW HVAC season is your most valuable 90 days. Every missed call is money walking to the guy down the street.\n\nSophie makes sure that doesn't happen. {booking_link}",
        },
        {
            "day": 12, "channel": "sms",
            "body": "Last message {firstName}. Whenever the timing's right. {booking_link}",
        },
    ],
    "roofer": [
        {
            "day": 0, "channel": "sms",
            "body": "Hey {firstName} — when you're on a roof and your phone rings, what happens to that call? {booking_link}",
        },
        {
            "day": 2, "channel": "email",
            "subject": "The roofer who stopped climbing down for calls",
            "body": "Roofer in Prosper. Every time the phone rang he had to climb down. Half the time the caller had already moved on.\n\nNow Sophie handles it. He stays on the roof. Jobs still get booked.\n\n{booking_link}",
        },
        {
            "day": 5, "channel": "sms",
            "body": "{firstName} — 2-minute demo. Hear Sophie on a real roofing call. {booking_link}",
        },
        {
            "day": 8, "channel": "email",
            "subject": "Storm season is coming",
            "body": "Your phone won't stop ringing. Neither does Sophie.\n\nWhen storm season hits, the roofers who answer every call are the ones who win. {booking_link}",
        },
        {
            "day": 12, "channel": "sms",
            "body": "Last one {firstName}. Sophie's ready when you are. {booking_link}",
        },
    ],
    "dental": [
        {
            "day": 0, "channel": "sms",
            "body": "Hey {firstName} — when you're with a patient and a new patient calls, what happens? {booking_link}",
        },
        {
            "day": 2, "channel": "email",
            "subject": "The dental practice that stopped losing new patients to voicemail",
            "body": "Dental practice in Frisco. New patients would call, get voicemail, and book somewhere else.\n\nNow Sophie answers every call and books appointments on the spot.\n\n{booking_link}",
        },
        {
            "day": 5, "channel": "sms",
            "body": "{firstName} — hear how Sophie sounds to a new patient calling your practice? {booking_link}",
        },
        {
            "day": 8, "channel": "email",
            "subject": "Every missed call is a missed new patient",
            "body": "They don't call back. They book somewhere else. That's just how it works.\n\nSophie makes sure that call never goes unanswered. {booking_link}",
        },
        {
            "day": 12, "channel": "sms",
            "body": "Last message {firstName}. {booking_link}",
        },
    ],
    "lawyer": [
        {
            "day": 0, "channel": "email",
            "subject": "The call that became a $40K case",
            "body": "PI attorney in Allen. Missed a call during a deposition. The caller signed with someone else. That was a $40K case.\n\nSophie prevents that. Every call answered. Every lead qualified. 24/7.\n\n{booking_link}",
        },
        {
            "day": 2, "channel": "sms",
            "body": "{firstName} — Sophie qualifies every inbound call so you only talk to real cases. {booking_link}",
        },
        {
            "day": 5, "channel": "email",
            "subject": "What Sophie tells a caller at 9pm",
            "body": "Accidents don't happen at 9am. When someone calls your firm at 9pm, Sophie answers professionally, qualifies the lead, and books a consult.\n\nYou wake up to a booked calendar. {booking_link}",
        },
        {
            "day": 8, "channel": "sms",
            "body": "{firstName} — 15 minutes to see it live. {booking_link}",
        },
        {
            "day": 12, "channel": "email",
            "subject": "Signing off — for now",
            "body": "Last message from me. The offer to see Sophie in action stands whenever you're ready.\n\nNo pressure. Just know that every unanswered call is a case walking out the door.\n\n{booking_link}",
        },
    ],
}

# The single tag Tyler stamps on every cold-pushed AIPG prospect. Tyler's
# cold push (tools/ghl.py create_contact) writes:  tyler-prospect + cold-email
# + aiphoneguy + {industry}.  There is NO compound `tyler-prospect-plumber` tag
# in live data — Randy used to search those compound keys and matched NOBODY.
# Randy now selects on this bare tag and derives the vertical from the separate
# industry tag (see INDUSTRY_TAG_TO_VERTICAL).
TYLER_PROSPECT_TAG = "tyler-prospect"

# Industry-tag → sequence-vertical map.
#
# Built from the ACTUAL tag vocabulary on the 179 live AIPG GHL contacts
# (locationId ZoxVB4ibMZZ2lZ5QpXep), probed read-only 2026-06-29. Distinct
# industry tags carried by tyler-prospect contacts and their live counts:
#   plumbing (45), plumber (41), roofing (35), hvac (27), dental (17),
#   personal-injury-law (2), plumbing/hvac (2), hvac/plumbing (1)
# `plumbing` and `plumber` are disjoint sets (no contact carries both) — both
# are the plumbing vertical. The combined `plumbing/hvac` / `hvac/plumbing`
# tags (3 contacts) are mapped to the first-named trade. Mapping TARGETS are the
# existing vertical names Randy's SEQUENCES expect (plumber/hvac/roofer/dental/
# lawyer) — those are unchanged.
INDUSTRY_TAG_TO_VERTICAL = {
    "plumbing": "plumber",
    "plumber": "plumber",
    "plumbing/hvac": "plumber",
    "roofing": "roofer",
    "roofer": "roofer",
    "hvac": "hvac",
    "hvac/plumbing": "hvac",
    "dental": "dental",
    "personal-injury-law": "lawyer",
    "lawyer": "lawyer",
}

# Tags that are NEVER an industry signal (so they're skipped when deriving the
# vertical from a contact's tag set). Everything Tyler/the pipeline stamps that
# isn't the trade.
NON_INDUSTRY_TAGS = frozenset({
    TYLER_PROSPECT_TAG, "cold-email", "aiphoneguy", "ai-phone-guy",
    "sequence-active", "sequence-complete", "csv-import", "dfw",
    "marcus-prospect", "callingdigital", "do-not-contact", "fabricated-pre-fix",
    "email-engaged", "clicked-demo-link", "replied", "intent-shown",
})


def vertical_for_tags(tags: list) -> str:
    """Derive a sequence vertical from a contact's full tag set.

    Returns the mapped vertical, or "" if no recognized industry tag is present
    (e.g. a stray med-spa / real-estate tag with no AIPG sequence). Case- and
    whitespace-insensitive.
    """
    normalized = [str(t).strip().lower() for t in (tags or [])]
    for t in normalized:
        if t in NON_INDUSTRY_TAGS:
            continue
        vert = INDUSTRY_TAG_TO_VERTICAL.get(t)
        if vert:
            return vert
    return ""


# Backwards-compat: the old compound-key map is retained ONLY so existing
# imports (e.g. scripts/reconcile_randy_enrollments.py) don't break. It is no
# longer how Randy finds prospects — it matched zero live contacts. New code
# should use TYLER_PROSPECT_TAG + vertical_for_tags().
TAG_TO_VERTICAL = {
    "tyler-prospect-plumber": "plumber",
    "tyler-prospect-hvac": "hvac",
    "tyler-prospect-roofer": "roofer",
    "tyler-prospect-dental": "dental",
    "tyler-prospect-lawyer": "lawyer",
}

# Send schedule per vertical (day_of_week, hour, minute) in CST
SEND_SCHEDULE = {
    "plumber":  {"day": "tuesday",   "hour": 18, "minute": 0},
    "hvac":     {"day": "thursday",  "hour": 18, "minute": 0},
    "roofer":   {"day": "wednesday", "hour": 16, "minute": 30},
    "dental":   {"day": "tuesday",   "hour": 11, "minute": 0},
    "lawyer":   {"day": "thursday",  "hour": 20, "minute": 0},
}


def get_sequence(vertical: str) -> list:
    return SEQUENCES.get(vertical, [])


def render_message(template: dict, contact: dict) -> dict:
    """Render a sequence step with contact data."""
    rendered = {}
    for key, val in template.items():
        if isinstance(val, str):
            rendered[key] = val.format(
                firstName=contact.get("firstName", ""),
                businessName=contact.get("businessName", ""),
                booking_link=BOOKING_LINK,
            )
        else:
            rendered[key] = val
    return rendered
