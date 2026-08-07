"""services/inbox_janitor.py -- archive machine noise out of the human inboxes.

The lead side of the mail problem is PUSH (the postal classifier escalates
prospects to Michael on its own rail), so noise costs no leads -- it costs
attention. This sweep moves known machine-noise threads out of INBOX into a
label, using the Gmail scopes the postal tokens already hold. Creating real
Gmail filters would need gmail.settings.basic and a re-consent click per
account; the janitor needs neither.

SAFETY RAILS:
  - archive-only: never deletes, never marks read; threads stay under the label
  - every query is pinned to a sender and runs with in:inbox enforced here
  - rules live in config/inbox_janitor.yaml (enabled: false kills the sweep)
  - dry-run by default from the admin endpoint; the scheduler commits
  - records an inbox_janitor heartbeat per completed sweep so the watchdog
    notices if the job dies (config heartbeats.inbox_janitor_max_age_hours)
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "config", "inbox_janitor.yaml")

# Per-rule cap per sweep. Backlog drains over a few sweeps instead of one huge
# mutation burst the first time the janitor meets months of accumulated noise.
_MAX_PER_RULE = 50


def _load_config() -> dict:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as e:
        logger.warning("[janitor] config unreadable, sweep disabled: %s", e)
        return {}


def run_sweep(*, commit: bool = False) -> Dict[str, Any]:
    """One janitor pass. Dry-run unless commit=True: reports what WOULD move
    without touching a thread. Returns a receipt per rule."""
    cfg = _load_config()
    if not cfg.get("enabled"):
        return {"ok": True, "skipped": "disabled in config"}
    label = str(cfg.get("label") or "Machines")
    from services import postal_inbox

    receipts: List[Dict[str, Any]] = []
    moved = 0
    errors = 0
    for account, rules in (cfg.get("accounts") or {}).items():
        for rule in (rules or []):
            name = rule.get("name") or "?"
            query = (rule.get("query") or "").strip()
            if not query:
                continue
            try:
                hits = postal_inbox.search(account, f"in:inbox {query}", limit=_MAX_PER_RULE)
            except Exception as e:
                logger.warning("[janitor] search failed for %s/%s: %s", account, name, e)
                errors += 1
                continue
            rule_moved = 0
            for t in hits:
                tid = t.get("id")
                if not tid:
                    continue
                if commit:
                    try:
                        postal_inbox.apply_label(account, tid, label)
                        postal_inbox.archive(account, tid)
                    except Exception as e:
                        logger.warning("[janitor] move failed for %s/%s/%s: %s",
                                       account, name, tid, e)
                        errors += 1
                        continue
                rule_moved += 1
            moved += rule_moved
            receipts.append({"account": account, "rule": name, "matched": len(hits),
                             "moved" if commit else "would_move": rule_moved})

    receipt = {"ok": True, "commit": commit, "label": label,
               "moved" if commit else "would_move": moved,
               "errors": errors, "rules": receipts}
    if commit:
        try:
            from services.watchdog import record_heartbeat
            record_heartbeat("inbox_janitor")
        except Exception:
            logger.warning("[janitor] heartbeat write failed (sweep itself succeeded)")
    logger.info("[janitor] sweep receipt: %s", receipt)
    return receipt


__all__ = ["run_sweep"]
