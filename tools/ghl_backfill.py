"""
tools/ghl_backfill.py — One-time backfill for $0 Tyler opportunities in GHL.

Finds open opportunities in the Lead Pipeline whose contact carries the
'tyler-prospect' tag and whose monetaryValue is 0, then sets the value to
$482 (AI Phone Guy standard MRR). Idempotent: re-running after success
finds zero candidates because the value filter excludes already-fixed opps.

Usage:
    python -m tools.ghl_backfill              # dry run (default)
    python -m tools.ghl_backfill --execute    # actually write to GHL

Requires env vars (already set in Railway): GHL_API_KEY, GHL_LOCATION_ID,
GHL_PIPELINE_ID.
"""

import os
import sys
import logging

from tools.ghl import _ghl_request

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

TARGET_TAG = "tyler-prospect"
TARGET_VALUE = 482


def list_pipeline_opportunities(location_id: str, pipeline_id: str) -> list:
    data = _ghl_request(
        "backfill_search_opps",
        "GET",
        "/opportunities/search",
        params={"location_id": location_id, "pipeline_id": pipeline_id, "limit": 100},
        timeout=20,
    )
    return data.get("opportunities", []) or []


def get_contact(contact_id: str) -> dict:
    data = _ghl_request(
        "backfill_get_contact",
        "GET",
        f"/contacts/{contact_id}",
        timeout=15,
    )
    return data.get("contact", data) or {}


def opp_tags(opp: dict) -> list:
    """Use inline contact tags if present, otherwise fetch the contact."""
    inline = opp.get("contact") or {}
    if inline.get("tags"):
        return [str(t).strip().lower() for t in inline["tags"]]
    contact_id = opp.get("contactId") or inline.get("id")
    if not contact_id:
        return []
    try:
        contact = get_contact(contact_id)
    except Exception as e:
        log.warning("contact %s lookup failed: %s", contact_id, e)
        return []
    return [str(t).strip().lower() for t in (contact.get("tags") or [])]


def update_opp_value(opportunity_id: str, value: int) -> dict:
    # PUT https://services.leadconnectorhq.com/opportunities/{id}
    # Body: {"monetaryValue": 482}
    return _ghl_request(
        "backfill_update_opp",
        "PUT",
        f"/opportunities/{opportunity_id}",
        json_body={"monetaryValue": value},
        timeout=15,
    )


def main(execute: bool) -> int:
    location_id = os.getenv("GHL_LOCATION_ID", "").strip()
    pipeline_id = os.getenv("GHL_PIPELINE_ID", "").strip()
    if not location_id or not pipeline_id:
        log.error("GHL_LOCATION_ID and GHL_PIPELINE_ID must be set.")
        return 2

    log.info("Fetching opportunities in pipeline %s ...", pipeline_id)
    opps = list_pipeline_opportunities(location_id, pipeline_id)
    log.info("Found %d total opportunities in pipeline.", len(opps))

    candidates = []
    for opp in opps:
        try:
            value = float(opp.get("monetaryValue") or 0)
        except (TypeError, ValueError):
            value = 0
        if value != 0:
            continue
        tags = opp_tags(opp)
        if TARGET_TAG not in tags:
            continue
        candidates.append({
            "id": opp.get("id"),
            "name": opp.get("name", ""),
            "current_value": value,
            "stage": opp.get("pipelineStageId", ""),
        })

    log.info("=== Backfill candidates: %d ===", len(candidates))
    for c in candidates:
        log.info("  %s | %s | $%s", c["id"], c["name"], c["current_value"])

    if not execute:
        log.info(
            "DRY RUN. Re-run with --execute to set monetaryValue=$%d on %d opps.",
            TARGET_VALUE, len(candidates),
        )
        return 0

    updated = 0
    failed = 0
    for c in candidates:
        try:
            update_opp_value(c["id"], TARGET_VALUE)
            log.info("Updated %s → $%d", c["id"], TARGET_VALUE)
            updated += 1
        except Exception as e:
            log.error("Failed to update %s: %s", c["id"], e)
            failed += 1

    log.info("=== Backfill complete: %d updated, %d failed ===", updated, failed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    execute_flag = "--execute" in sys.argv
    sys.exit(main(execute_flag))
