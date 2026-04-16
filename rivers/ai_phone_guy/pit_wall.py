"""Joshua — Pit Wall RevOps for The AI Phone Guy.

Monitors Tyler's Instantly campaign. Produces Race Report.
Updates GHL contact tags with grid positions.

Schedule: Every 2 hours.
"""

import os
import logging
import requests
from rivers.shared.pit_wall import pull_instantly_leads, build_race_report

logger = logging.getLogger(__name__)

GHL_BASE = "https://services.leadconnectorhq.com"


def _ghl_headers() -> dict:
    key = (os.getenv("GHL_API_KEY") or "").strip()
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Version": "2021-07-28"}


def _ghl_location() -> str:
    return (os.getenv("GHL_LOCATION_ID") or "").strip()


def _update_ghl_tags(grid: list):
    """Update GHL contacts with pit wall grid position tags."""
    if not _ghl_location():
        return 0

    updated = 0
    for entry in grid:
        try:
            r = requests.get(
                f"{GHL_BASE}/contacts/",
                headers=_ghl_headers(),
                params={"locationId": _ghl_location(), "query": entry["email"], "limit": 1},
                timeout=10,
            )
            if r.status_code != 200:
                continue
            contacts = r.json().get("contacts", [])
            if not contacts:
                continue

            contact_id = contacts[0]["id"]
            old_tags = [t for t in contacts[0].get("tags", []) if t.startswith("pit-")]

            if old_tags:
                requests.delete(
                    f"{GHL_BASE}/contacts/{contact_id}/tags",
                    headers=_ghl_headers(), json={"tags": old_tags}, timeout=10,
                )
            requests.post(
                f"{GHL_BASE}/contacts/{contact_id}/tags",
                headers=_ghl_headers(), json={"tags": [entry["tag"]]}, timeout=10,
            )
            updated += 1
        except Exception as e:
            logger.warning(f"[Joshua] GHL tag update failed for {entry['email']}: {e}")

    return updated


def joshua_run() -> dict:
    """Joshua's pit wall run — pull telemetry, classify, update GHL, report."""
    api_key = (os.getenv("INSTANTLY_API_KEY_TYLER") or "").strip()
    campaign_id = (os.getenv("INSTANTLY_CAMPAIGN_TYLER") or "").strip()

    if not api_key or not campaign_id:
        logger.warning("[Joshua] Instantly not configured — skipping.")
        return {"status": "skipped"}

    leads = pull_instantly_leads(api_key, campaign_id)
    if not leads:
        logger.info("[Joshua] No leads in campaign yet.")
        return {"status": "ok", "leads": 0, "report": "No leads in race yet."}

    result = build_race_report("Joshua", "The AI Phone Guy", leads)
    ghl_updates = _update_ghl_tags(result["grid"])
    result["ghl_updates"] = ghl_updates

    logger.info(f"[Joshua] Race Report:\n{result['report']}")
    return result
