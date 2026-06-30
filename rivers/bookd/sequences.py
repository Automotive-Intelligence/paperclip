"""Intent-signal → outbound-angle map for Book'd (Cole's lane).

IRON RULES (per Cole's prompt, bookd_agent_fleet_spec_2026-06-24.md §4):
- A contact with NO observed DataMoon signal does NOT enter a sequence. Period.
- NEVER fabricate numbers; no customer-named claim without Ryan verification.
- NEVER send from the bookd.cx primary domain — only meetbookd.com / powerbookd.com.
- 14-day mailbox warmup before any real volume (warmup completes ~2026-07-06).
- Every message is peer-operator-to-peer-operator, no guru, no em-dashes,
  no exclamation marks, and references the observed intent signal directly.
- EXCLUDE captive carriers entirely (see CAPTIVE_CARRIER_EXCLUSIONS).

Unlike the AI Phone Guy river (GHL-tag driven), Book'd outbound runs through
Instantly and syncs to Twenty (Book'd workspace). This module is the shared
signal-to-angle reference; Cole authors the actual sequence copy per gate pass,
and Hayes segments DataMoon signals into Cole's send queue.
"""

import os

# Book'd booking / demo CTA target (Book a 20-minute demo).
BOOKING_LINK = os.environ.get("BOOKING_LINK_BOOKD", "")

# DataMoon B2B intent topic IDs → ICP signal label.
DATAMOON_TOPIC_IDS = {
    "13635": "Agency Management System",
    "27554": "Lead Management Software",
    "26165": "Insurance Software",
    "27799": "Life & Health Insurance Agency Management Software",
    "47780": "Independent Insurance Agent Growth Strategies",
}

# Signal → outbound angle (Cole references the observed signal directly).
SIGNAL_TO_ANGLE = {
    "Agency Management System": (
        "Book'd plugs into your AMS so bookings live where the policy work lives."
    ),
    "Lead Management Software": (
        "Speed-to-lead is the entire game in final-expense. "
        "Book'd answers + qualifies in seconds."
    ),
    "Insurance Software": (
        "Less time stitching tools. More time selling."
    ),
    "Life & Health Insurance Agency Management Software": (
        "Agency-grade calendar + intake without the agency-grade price tag."
    ),
    "Independent Insurance Agent Growth Strategies": (
        "The bottleneck isn't leads, it's getting them on the calendar. "
        "Book'd is the calendar piece."
    ),
}

# Permanent exclusion — captive carriers can't switch CRMs, never target them.
CAPTIVE_CARRIER_EXCLUSIONS = {
    "New York Life",
    "State Farm",
    "Northwestern Mutual",
    "Primerica",
    "Globe Life",
    "American Income Life",
}

# Sending domains (NEVER bookd.cx primary).
SENDING_DOMAINS = ("meetbookd.com", "powerbookd.com")


def angle_for_signal(signal_label: str) -> str:
    """Return the outbound angle for an observed DataMoon signal label."""
    return SIGNAL_TO_ANGLE.get(signal_label, "")


def angle_for_topic_id(topic_id: str) -> str:
    """Return the outbound angle for a DataMoon topic ID."""
    return angle_for_signal(DATAMOON_TOPIC_IDS.get(str(topic_id), ""))


def is_captive_excluded(carrier: str) -> bool:
    """True if the carrier is on the permanent captive-carrier kill list."""
    return (carrier or "").strip() in CAPTIVE_CARRIER_EXCLUSIONS
