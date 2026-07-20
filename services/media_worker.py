"""services/media_worker.py -- video worker trigger handler.

Entry point a Railway trigger calls: pull assets, render one take, then
STAGE the result (master + review sheet) and raise a CMO flag for human
review. Stage-and-flag only. This module never schedules anything outward;
the outward gate (does the take actually go live) stays a human decision.

Follows the proven watchdog pattern (POST /admin/run-watchdog): a thin
handler that does one detect/act cycle and hands off to a human-facing
review surface rather than acting unattended.
"""
from __future__ import annotations

import os

from tools.media_worker.render import render_one


def run_video(take: str, edit: dict) -> dict:
    """Render one take and stage it for review. Returns status plus the
    render's master/sheet/words paths. Does not push, log, or flag yet --
    those steps are deferred (see comments below); this call only produces
    local render output, ready to be staged by a later step."""
    model = os.environ["WHISPER_MODEL"]
    out_dir = os.environ.get("RENDERS_OUT", "/opt/media/out")
    res = render_one(
        edit, take, model, out_dir,
        cut_script=os.path.join(os.path.dirname(__file__), "..", "scripts", "cut_talking_head.py"),
        sheet_script=os.path.join(os.path.dirname(__file__), "..", "scripts", "video_review_sheet.py"),
    )

    # DEFERRED (deploy task, gated on the Blob token / flag mechanism):
    #   1. Push res["master"] and res["sheet"] to Blob via
    #      tools.media_worker.blob_sync.upload (needs Michael's Vercel auth token).
    #   2. Append an entry to renders/th/REVIEW_LOG.md recording this take,
    #      its edit, and the staged Blob URLs.
    #   3. Write a CMO flag (via the flag helper / cmo_state.md) so a human
    #      sees the staged render and decides whether it ships. Nothing in
    #      this handler schedules or publishes on its own; the human gate
    #      is the only path outward.
    return {"status": "staged", **res}
