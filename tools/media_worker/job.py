"""tools/media_worker/job.py -- the Railway one-shot: render ONE take pulled
from Blob and push the master + review sheet back.

Env:
  BLOB_READ_WRITE_TOKEN  required
  TAKE_PATHNAME          required; the Blob pathname of the raw take
  BRAND                  default "aipg"
  MODEL_PREFIX           default "stock_library/.whisper_models/"
  WORK                   default "/opt/media/work"
  OUT_PREFIX             default "renders_th/"
  EDIT_JSON              optional JSON string; default {"brand": BRAND}

Flow: pull the whisper model (only if not already local), pull the take,
render_one() (transcribe -> cut -> contact sheet), push the master + sheet
back to Blob, print MASTER_URL / SHEET_URL / a REVIEW_LOG-style summary line.
No scheduling, no stock/b-roll in v1: a plain talking-head cut renders fine
on its own."""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import traceback
from datetime import datetime, timezone

from tools.media_worker.blob_http import blob_list, blob_download, blob_put
from tools.media_worker.render import render_one

MODEL_BASENAME = "ggml-small.en.bin"
CUT_SCRIPT = "/app/scripts/cut_talking_head.py"
SHEET_SCRIPT = "/app/scripts/video_review_sheet.py"


def ensure_model(model_prefix: str, work: str, token: str, lister, downloader) -> str:
    """Return the local whisper model path, pulling it from Blob under
    `model_prefix` only if it is not already there (so a warm worker does
    not re-download the ~487MB model)."""
    dest = os.path.join(work, "model", MODEL_BASENAME)
    if os.path.exists(dest):
        return dest
    blobs = lister(model_prefix, token)
    match = next((b for b in blobs if b["pathname"].endswith(".bin")), None)
    if not match:
        raise RuntimeError(f"no .bin model found under Blob prefix {model_prefix!r}")
    downloader(match["url"], dest, token)
    return dest


def fetch_take(take_pathname: str, work: str, token: str, lister, downloader) -> str:
    """Download the raw take at the exact Blob pathname `take_pathname` to
    WORK/take.mp4 and return the local path. Lists the PARENT prefix, not the
    full pathname: Vercel Blob's prefix filter returns the children under a
    prefix and excludes an exact full-key match, so listing the full pathname
    returns 0 results."""
    parent = take_pathname.rsplit("/", 1)[0] + "/" if "/" in take_pathname else ""
    blobs = lister(parent, token)
    match = next((b for b in blobs if b["pathname"] == take_pathname), None)
    if not match:
        raise RuntimeError(f"take not found on Blob: {take_pathname!r}")
    dest = os.path.join(work, "take.mp4")
    downloader(match["url"], dest, token)
    return dest


def push_outputs(res: dict, out_prefix: str, take_pathname: str, token: str, putter) -> dict:
    """Push the rendered master + review sheet to Blob under
    `<out_prefix><take-stem>.mp4` / `.review.png`, return their URLs."""
    stem = pathlib.Path(take_pathname).stem
    master_url = putter(res["master"], f"{out_prefix}{stem}.mp4", token)
    sheet_url = putter(res["sheet"], f"{out_prefix}{stem}.review.png", token)
    return {"master_url": master_url, "sheet_url": sheet_url}


def append_blob_review_log(entry: str, token: str, prefix: str = "renders_th/") -> None:
    """Append a timestamped `entry` line to the Blob object `<prefix>REVIEW_LOG.md`,
    in the `\\n- <utc iso> <entry>\\n` line format, backed by Blob instead of a
    local path.
    Downloads the existing REVIEW_LOG.md first (empty string if it does not
    exist yet on Blob), appends, and pushes the combined content back. This
    is the stage-and-flag: a human/watchdog reads it later, nothing here
    schedules or publishes anything. datetime.now() is called here, inside
    the function, not at module scope or as a default-argument value."""
    pathname = f"{prefix}REVIEW_LOG.md"
    existing = ""
    blobs = blob_list(prefix, token)
    match = next((b for b in blobs if b["pathname"] == pathname), None)
    if match:
        fd, tmp_dest = tempfile.mkstemp(suffix=".md")
        os.close(fd)
        try:
            blob_download(match["url"], tmp_dest, token)
            with open(tmp_dest, "r", encoding="utf-8") as f:
                existing = f.read()
        finally:
            if os.path.exists(tmp_dest):
                os.remove(tmp_dest)

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    updated = existing + f"\n- {stamp} {entry}\n"

    fd, local_path = tempfile.mkstemp(suffix=".md")
    os.close(fd)
    try:
        with open(local_path, "w", encoding="utf-8") as f:
            f.write(updated)
        blob_put(local_path, pathname, token)
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)


def run_job(env: dict) -> dict:
    """The full one-shot flow, env-driven so main() and tests share one
    path. References blob_list/blob_download/blob_put/render_one by module
    global (not as default-argument values), so tests can monkeypatch this
    module's attributes and have run_job pick up the stubs."""
    token = env["BLOB_READ_WRITE_TOKEN"]
    take_pathname = env["TAKE_PATHNAME"]
    brand = env.get("BRAND", "aipg")
    model_prefix = env.get("MODEL_PREFIX", "stock_library/.whisper_models/")
    work = env.get("WORK", "/opt/media/work")
    out_prefix = env.get("OUT_PREFIX", "renders_th/")
    edit = json.loads(env["EDIT_JSON"]) if env.get("EDIT_JSON") else {"brand": brand}

    print(f"[job] pulling whisper model from {model_prefix}", flush=True)
    model = ensure_model(model_prefix, work, token, blob_list, blob_download)
    print(f"[job] pulling take {take_pathname}", flush=True)
    take = fetch_take(take_pathname, work, token, blob_list, blob_download)
    print(f"[job] rendering brand={brand} edit={edit}", flush=True)
    res = render_one(edit, take, model, work, cut_script=CUT_SCRIPT, sheet_script=SHEET_SCRIPT)
    print(f"[job] pushing master + sheet to {out_prefix}", flush=True)
    urls = push_outputs(res, out_prefix, take_pathname, token, blob_put)

    print(f"MASTER_URL={urls['master_url']}")
    print(f"SHEET_URL={urls['sheet_url']}")
    print(f"REVIEW_LOG: take={take_pathname} edit={edit} "
          f"master={urls['master_url']} sheet={urls['sheet_url']}")

    entry = (f"STAGED brand={brand} take={take_pathname} master={urls['master_url']} "
             f"sheet={urls['sheet_url']} :: awaiting file-133/117 + CMO gate, NOT scheduled")
    try:
        append_blob_review_log(entry, token, out_prefix)
    except Exception:
        # The master is already safely on Blob; a REVIEW_LOG write failure
        # must not fail the job, only get logged for follow-up.
        print("[job] REVIEW_LOG update FAILED (non-fatal)", flush=True)
        traceback.print_exc()

    return urls


def main() -> int:
    run_job(os.environ)
    return 0


if __name__ == "__main__":
    sys.exit(main())
