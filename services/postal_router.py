"""Postal Agent router — maps (account, category) to destinations + writes them.

Per Postal Agent plan Q2: intent-data replies route to Twenty + AVO chat;
other categories route per the v1 default config below.

Phase 3 RESOLVED the destination but logged only. Phase 4 (this file +
services/postal_writers.py) actually performs the writes, gated behind the
POSTAL_WRITES_ENABLED env flag so the side effects can be switched on once
we've watched real classifications and trust the routing. When the flag is
off, route_to_destinations falls back to the old log-only behaviour.

Destination types (string codes stored in postal_processed.routed_to):
    twenty:<workspace>          — create/dedupe a Person in the Twenty workspace
    avo_chat:<channel>          — post a Slack message to a persona chat
    pit_wall                    — post a Slack message to #pit-wall
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
    """Resolve destinations for (account, category) and execute the writers.

    Returns:
        (destinations, side_effects_taken)

    `destinations` is the resolved routing list. `side_effects_taken` is what
    actually completed — the Phase 4 writers (services/postal_writers) only
    return the destinations whose side effect succeeded, so postal_processed
    records the true audit trail. When POSTAL_WRITES_ENABLED is off the writers
    no-op and return the intended destinations tagged `dry-run:` instead.
    """
    from services.postal_writers import execute_destinations

    destinations = resolve(account_label, category)
    thread_id = thread_meta.get("id", "?")
    sender = thread_meta.get("sender", "?")
    subject = (thread_meta.get("subject") or "")[:80]

    completed, failed = execute_destinations(
        account_label, category, destinations, thread_meta
    )

    logger.info(
        f"postal route: account={account_label} category={category} "
        f"thread={str(thread_id)[:16]} sender={sender} subject={subject!r} "
        f"resolved={destinations} completed={completed}"
        + (f" failed={failed}" if failed else "")
    )

    return destinations, completed
