"""Data-quality + channel-routing guardrails for AIPG (AI Phone Guy) prospects.

Boardroom-identified send blocker (2026-07-01). Before this module, Randy
(``workflow.py``) enrolled every ``tyler-prospect`` contact with a recognized
trade into an ICP sequence whose steps interleave SMS and EMAIL — with no
data-quality gate. Live probe of the 187 ``tyler-prospect`` GHL contacts (loc
``ZoxVB4ibMZZ2lZ5QpXep``, read-only 2026-07-01) found:

  * 61 with NO email — yet still sent the EMAIL steps of their sequence.
  * 13 with free/personal email (gmail/yahoo/outlook) — low-trust B2B, and a
    tell that the owner is phone-first.
  * 7 with invalid North-American phone area codes: 875 (x5), 995 (x1),
    120 (x1) — codes that do not exist in the NANP.
  * 70 with the COMPANY NAME parked in the person-name field (so ``{firstName}``
    rendered "Little" / "Celina" / "Aubrey" fragments).

AIPG SELLS an AI phone receptionist. Phone-first local trades (plumbers, HVAC,
roofers) with no business email should be routed to an SMS/CALL lane — the
channel that matches the product — not aimed at email.

These are PURE functions (no I/O) so they are trivially unit-testable and can be
wired as guardrails in the enrollment path. This module never mutates GHL; the
channel decision is recorded on the in-memory contact dict (``_channel``) and on
the durable enrollment record, and surfaced via lane tags that a future call-lane
consumes.
"""

import re
from typing import Optional

# --- Channel-routing lane tags (what a future SMS/CALL lane will consume) ------
# There is NO existing lane tag in live GHL vocabulary (probed 2026-07-01), so we
# introduce these. They are recorded on the enrollment record / contact dict, not
# written back to GHL in this change (read-only).
EMAIL_LANE_TAG = "aipg-email-lane"
SMS_LANE_TAG = "aipg-sms-lane"
EXCLUDED_LANE = "excluded"

CHANNEL_EMAIL = "email"
CHANNEL_SMS = "sms"
CHANNEL_EXCLUDED = "excluded"

# Free / personal mailbox providers. A B2B prospect on one of these is treated as
# phone-first (route to SMS), not emailed as a business address.
FREE_EMAIL_DOMAINS = frozenset({
    "gmail.com", "yahoo.com", "hotmail.com", "aol.com", "outlook.com",
    "icloud.com", "msn.com", "live.com", "comcast.net", "att.net",
    "sbcglobal.net", "verizon.net", "me.com", "ymail.com", "protonmail.com",
})

# Confirmed-bogus area codes seen in live AIPG data that are not valid NANP codes.
# 875 / 995 are unassigned; 120 is not a valid area code (area codes never start
# with 0/1). Kept explicit so the intent is obvious even though the general NANP
# rule below also rejects 120.
KNOWN_BAD_AREA_CODES = frozenset({"875", "995", "120"})

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# --- Email --------------------------------------------------------------------
def normalize_email(email: Optional[str]) -> str:
    return (email or "").strip().lower()


def _email_domain(email: Optional[str]) -> str:
    e = normalize_email(email)
    return e.split("@", 1)[1] if "@" in e else ""


def is_valid_email_shape(email: Optional[str]) -> bool:
    return bool(_EMAIL_RE.match(normalize_email(email)))


def is_free_email(email: Optional[str]) -> bool:
    return _email_domain(email) in FREE_EMAIL_DOMAINS


def is_valid_business_email(email: Optional[str]) -> bool:
    """A validated business email: well-formed AND not a free-mail provider.

    This is the gate for the EMAIL sequence: no validated business email => the
    contact never enters (or, at send time, is never sent) an email step.
    """
    return is_valid_email_shape(email) and not is_free_email(email)


# --- Phone --------------------------------------------------------------------
def phone_digits(phone: Optional[str]) -> str:
    return re.sub(r"\D", "", phone or "")


def phone_area_code(phone: Optional[str]) -> str:
    d = phone_digits(phone)
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d[:3] if len(d) >= 10 else ""


def is_valid_na_phone(phone: Optional[str]) -> bool:
    """True if this looks like a dialable North-American number.

    NANP rules enforced:
      * 10 digits, or 11 with a leading country-code 1.
      * Area code (NPA) first digit is 2-9 (never 0 or 1).
      * Area code is not an N11 service code (211/311/.../911).
      * Not in the confirmed-bogus set (875/995/120).
      * Exchange (NXX) first digit is 2-9.
    """
    d = phone_digits(phone)
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    if len(d) != 10:
        return False
    npa, nxx = d[:3], d[3:6]
    if npa[0] in "01":
        return False
    if npa[1:] == "11":  # N11 service codes
        return False
    if npa in KNOWN_BAD_AREA_CODES:
        return False
    if nxx[0] in "01":
        return False
    return True


def is_bad_phone(phone: Optional[str]) -> bool:
    """True if a phone value is present but not a valid NA number.

    (Absent phone is not "bad" — it just means no phone; callers distinguish.)
    """
    return bool((phone or "").strip()) and not is_valid_na_phone(phone)


def has_valid_phone(phone: Optional[str]) -> bool:
    return is_valid_na_phone(phone)


# --- Name-field parsing -------------------------------------------------------
def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def looks_like_company_in_name(contact: dict) -> bool:
    """True if the person-name field is actually the company name.

    Tyler's ``create_contact`` falls back to splitting the BUSINESS name into
    first/last when no real contact name is found, so ``firstName`` ends up being
    a company fragment (e.g. "Little" from "Little Elm Dental Studio").
    """
    company = _norm(contact.get("companyName"))
    if not company:
        return False
    fn = _norm(contact.get("firstName"))
    ln = _norm(contact.get("lastName"))
    full = (fn + " " + ln).strip()
    return company in (fn, ln, full)


def clean_name_fields(contact: dict) -> dict:
    """Return a shallow copy with company-in-name defused.

    When the name is really the company, blank ``firstName`` so ``{firstName}``
    merge tags don't render a company fragment. ``companyName`` is preserved (the
    ``{businessName}`` merge tag still works). Non-destructive to GHL.
    """
    if not looks_like_company_in_name(contact):
        return contact
    cleaned = dict(contact)
    cleaned["firstName"] = ""
    cleaned["_name_cleaned"] = True
    return cleaned


# --- Dedup --------------------------------------------------------------------
def dedup_key(contact: dict) -> str:
    """Normalized identity key for de-duplication.

    Prefers company+email+phone; a contact matching any prior contact on this
    composite is a duplicate. Whitespace/case-insensitive; phone reduced to
    digits so "+1 (972) 555-0100" == "9725550100".
    """
    company = _norm(contact.get("companyName"))
    email = normalize_email(contact.get("email"))
    # Reduce to the 10-digit NANP form so "+1 (972) 555-0100" == "9725550100".
    phone = phone_digits(contact.get("phone"))
    if len(phone) == 11 and phone.startswith("1"):
        phone = phone[1:]
    return f"{company}|{email}|{phone}"


# --- Channel routing (the strategic fix) --------------------------------------
def route_channel(contact: dict) -> str:
    """Decide the outreach channel for a contact.

      * Valid BUSINESS email        -> ``"email"``
      * else, valid NA phone        -> ``"sms"`` (phone-first: matches AIPG's product)
      * else (no reachable channel) -> ``"excluded"``
    """
    if is_valid_business_email(contact.get("email")):
        return CHANNEL_EMAIL
    if has_valid_phone(contact.get("phone")):
        return CHANNEL_SMS
    return CHANNEL_EXCLUDED


def lane_tag_for_channel(channel: str) -> str:
    return {
        CHANNEL_EMAIL: EMAIL_LANE_TAG,
        CHANNEL_SMS: SMS_LANE_TAG,
    }.get(channel, EXCLUDED_LANE)


def screen_contact(contact: dict) -> dict:
    """Run the full guardrail screen on a single contact.

    Returns a dict:
      {
        "contact": <cleaned contact dict with _channel + _lane_tag set>,
        "channel": "email" | "sms" | "excluded",
        "reasons": [ ... human-readable notes ... ],
        "bad_phone": bool,
        "name_cleaned": bool,
      }

    Does NOT dedup (that is cross-contact; the caller handles it with
    ``dedup_key``). Does NOT touch GHL.
    """
    reasons = []
    cleaned = clean_name_fields(contact)
    name_cleaned = bool(cleaned.get("_name_cleaned"))
    if name_cleaned:
        reasons.append("company-name-in-person-field cleaned")

    bad_phone = is_bad_phone(cleaned.get("phone"))
    if bad_phone:
        reasons.append(f"invalid phone area code ({phone_area_code(cleaned.get('phone')) or '??'})")

    email = cleaned.get("email")
    if not (email or "").strip():
        reasons.append("no email")
    elif is_free_email(email):
        reasons.append("free/personal email (not business)")
    elif not is_valid_email_shape(email):
        reasons.append("malformed email")

    channel = route_channel(cleaned)
    if channel == CHANNEL_SMS:
        reasons.append("routed to SMS/CALL lane (phone-first, no business email)")
    elif channel == CHANNEL_EXCLUDED:
        reasons.append("no reachable channel (no business email, no valid phone)")

    cleaned = dict(cleaned)
    cleaned["_channel"] = channel
    cleaned["_lane_tag"] = lane_tag_for_channel(channel)
    return {
        "contact": cleaned,
        "channel": channel,
        "reasons": reasons,
        "bad_phone": bad_phone,
        "name_cleaned": name_cleaned,
    }
