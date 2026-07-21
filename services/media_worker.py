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
from datetime import datetime, timezone

from tools.media_worker.render import render_one


def run_video(take: str, edit: dict) -> dict:
    """Render one take and stage it for review. Returns status plus the
    render's master/sheet/words paths. Runs the three stage-and-flag steps
    (Blob push, REVIEW_LOG, CMO flag) after render_one, then hands off to a
    human: nothing here schedules or publishes anything outward."""
    model = os.environ["WHISPER_MODEL"]
    out_dir = os.environ.get("RENDERS_OUT", "/opt/media/out")
    res = render_one(
        edit, take, model, out_dir,
        cut_script=os.path.join(os.path.dirname(__file__), "..", "scripts", "cut_talking_head.py"),
        sheet_script=os.path.join(os.path.dirname(__file__), "..", "scripts", "video_review_sheet.py"),
    )

    # Step 1: push master + sheet to Blob, but only when a token is actually
    # configured. Default (no token) path is safe: skip the push, still
    # stage locally + flag below. Import kept local so unit tests that never
    # take this branch do not need tools.media_worker.blob_sync importable.
    if os.environ.get("BLOB_READ_WRITE_TOKEN"):
        from tools.media_worker.blob_sync import upload as _blob_upload
        stage_to_blob({"master": res.get("master"), "sheet": res.get("sheet")}, _blob_upload)

    # Step 2: record this take in the human-facing review log.
    log_path = os.environ.get("REVIEW_LOG_PATH", "renders/th/REVIEW_LOG.md")
    append_review_log(f"take={take} edit={edit} master={res.get('master')} sheet={res.get('sheet')}", log_path)

    # Step 3: raise a CMO flag so a human sees the staged render. Never
    # schedules or publishes; the human gate is the only path outward.
    state_path = os.environ.get("CMO_STATE_PATH", "cmo_state.md")
    write_cmo_flag(f"video staged: take={take} master={res.get('master')} sheet={res.get('sheet')}", state_path)

    return {"status": "staged", "master": res.get("master"), "sheet": res.get("sheet"), "flag": True}


def append_review_log(entry: str, log_path: str) -> None:
    """Append a timestamped entry to the review log (creates parent dirs)."""
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"\n- {stamp} {entry}\n")


def write_cmo_flag(text: str, state_path: str) -> None:
    """Append a stage-and-flag entry to the CMO state file. Never schedules."""
    with open(state_path, "a", encoding="utf-8") as f:
        f.write(f"\n VIDEO WORKER FLAG: {text}\n")


def stage_to_blob(paths: dict, upload) -> dict:
    """Push the master + sheet to Blob via the injected upload() (blob_sync.upload)."""
    files = [paths[k] for k in ("master", "sheet") if paths.get(k)]
    root = os.path.dirname(files[0]) if files else "."
    manifest = os.path.join(root, ".blob_manifest.json")
    upload(files, root, manifest)
    return {"blob_pushed": files}
