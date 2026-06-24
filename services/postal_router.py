"""Postal Agent router — maps (account, category) to destinations.

Per Postal Agent plan Q2: intent-data replies route to Twenty + AVO chat;
other categories route per the v1 default config below.

V1 SCOPE (this file): RESOLVE the destination but DON'T WRITE to it yet.
Actual writes to Twenty CRM / AVO Slack / Pit Wall happen in Phase 4 once
we've watched real classifications for a day and verified routing works
the way we expect. For v1, the router logs the intended destination + the
agent stores it in postal_processed.routed_to for audit.

Destination types (string codes stored in postal_processed.routed_to):
    twenty:<workspace>          — would create Person + Conversation in Twenty
    avo_chat:<channel>          — would post a Slack message to a persona chat
    pit_wall                    — would surface as a flag in infrastructure_state.md
    label_only                  — only Gmail label applied; no downstream
    archive                     — labeled and archived; no downstream
    skip                        — explicitly do nothing
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ----- V1 default routing table -----
# Maps (account_label, category) → destination string.
# Anything not matched falls through to DEFAULT_ROUTES[category].

DEFAULT_ROUTES: dict[str, list[str]] = {
    # Routing destinations are LISTS — one classification can fan out
    "intent_reply":    ["twenty:wd", "avo_chat:revenue-sales", "label_only"],
    "lead_response":   ["twenty:wd", "avo_chat:revenue-sales", "label_only"],
    "billing":         ["pit_wall", "label_only"],
    "security":        ["pit_wall", "label_only"],
    "vendor_alert":    ["label_only"],
    "newsletter":      ["archive"],
    "transactional":   ["label_only"],
    "meeting":         ["label_only"],
    "junk":            ["archive"],
    "other":           ["label_only"],
}

# Per-account overrides (account_label → category → destinations)
ACCOUNT_OVERRIDES: dict[str, dict[str, list[str]]] = {
    "avi": {
        "intent_reply":  ["twenty:avi", "avo_chat:revenue-sales", "label_only"],
        "lead_response": ["twenty:avi", "avo_chat:revenue-sales", "label_only"],
    },
    "bookd": {
        "intent_reply":  ["twenty:bookd", "avo_chat:revenue-sales", "label_only"],
        "lead_response": ["twenty:bookd", "avo_chat:revenue-sales", "label_only"],
    },
    "aipg": {
        "intent_reply":  ["avo_chat:revenue-sales", "label_only"],
        "lead_response": ["avo_chat:revenue-sales", "label_only"],
    },
    "agentempire": {
        "lead_response": ["avo_chat:agent-empire", "label_only"],
    },
    "salesdroid": {
        # Personal / legacy account — most stuff is noise. Default heavy archive.
        "newsletter":    ["archive"],
        "vendor_alert":  ["archive"],
    },
}


def resolve(account_label: str, category: str) -> list[str]:
    """Return list of destination codes for an (account, category) pair.

    Falls through to DEFAULT_ROUTES[category] if no per-account override.
    """
    overrides = ACCOUNT_OVERRIDES.get(account_label, {})
    if category in overrides:
        return list(overrides[category])
    return list(DEFAULT_ROUTES.get(category, ["label_only"]))


def route_to_destinations(
    account_label: str,
    category: str,
    thread_meta: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """V1: resolve destinations + log them. NO actual writes to Twenty/Slack/PW yet.

    Returns:
        (destinations, side_effects_taken)

    side_effects_taken is the list of destinations that were *intended* in v1
    so postal_processed can record the audit trail. In Phase 4 this will
    actually invoke the writers and only return successful ones.
    """
    destinations = resolve(account_label, category)
    thread_id = thread_meta.get("id", "?")
    sender = thread_meta.get("sender", "?")
    subject = (thread_meta.get("subject") or "")[:80]

    logger.info(
        f"postal route v1 (LOG ONLY): account={account_label} category={category} "
        f"thread={thread_id[:16]} sender={sender} subject={subject!r} "
        f"would_route_to={destinations}"
    )

    # In v1, we record the *intended* destinations as if they ran.
    # Phase 4 will replace this with actual destination writers.
    return destinations, destinations
