"""Pit Wall — RevOps telemetry for Book'd (Hayes's deliverability lane).

Mirrors rivers/ai_phone_guy/pit_wall.py (Joshua) but adapted to Book'd:
  - Pulls Cole's Instantly (Book'd workspace) campaign and produces a Race Report
    via the shared rivers/shared/pit_wall builder.
  - Book'd is Twenty-based, NOT GHL — so unlike Joshua this does NOT write grid
    tags back to a GHL location. Hayes owns the Twenty (Book'd) sync separately;
    this surface is read-only deliverability telemetry feeding the morning brief.

Outbound is HELD until the meetbookd.com / powerbookd.com mailboxes finish
warmup (~2026-07-06). Until a campaign exists + has leads, this returns a
skip/no-leads status (never errors) so the agent log stays clean pre-launch.

Schedule: every 2 hours (matches Joshua's cadence).
"""

import os
import logging
from rivers.shared.pit_wall import pull_instantly_leads, build_race_report

logger = logging.getLogger(__name__)


def hayes_pit_wall_run() -> dict:
    """Pull Book'd Instantly telemetry, classify, and produce a Race Report.

    Returns a status dict. Never raises on missing config — Book'd outbound is
    warmup-gated, so 'skipped' / 'no leads yet' are expected pre-launch states.
    """
    api_key = (os.getenv("INSTANTLY_API_KEY_COLE") or os.getenv("INSTANTLY_API_KEY_BOOKD") or "").strip()
    campaign_id = (os.getenv("INSTANTLY_CAMPAIGN_COLE") or os.getenv("INSTANTLY_CAMPAIGN_BOOKD") or "").strip()

    if not api_key or not campaign_id:
        logger.info("[Hayes/PitWall] Book'd Instantly not configured (warmup-gated) — skipping.")
        return {"status": "skipped", "reason": "Instantly Book'd campaign not configured (pre-warmup)."}

    leads = pull_instantly_leads(api_key, campaign_id)
    if not leads:
        logger.info("[Hayes/PitWall] No leads in Book'd campaign yet (mailboxes warming).")
        return {"status": "ok", "leads": 0, "report": "No leads in race yet — Book'd mailboxes still warming."}

    result = build_race_report("Hayes", "Book'd", leads)
    logger.info(f"[Hayes/PitWall] Race Report:\n{result['report']}")
    return result
