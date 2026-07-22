"""tools/media_worker/poller.py -- Blob-poll auto-trigger.

Finds takes dropped under the render-queue prefix on Blob that have NOT been
rendered yet (no master at ``<out_prefix><stem>.mp4``) and renders up to
``limit`` of them via run_job. Idempotent -- the master IS the receipt, so a
rare double render just overwrites (blob_put sets x-allow-overwrite). Stage-and-
flag only: rendering a queued take stages master+sheet + appends REVIEW_LOG,
never schedules or publishes; the poller only decides WHICH take to render, not
whether it ships.

The CALLER (asgi POST /poll) owns the render lock + the background thread; this
module is pure pick + render logic so it unit-tests without a server.

Idempotency is stem-based, matching push_outputs (master = ``<out_prefix><stem>``):
two queued takes that share a basename map to one master, so the second is seen
as already-rendered. Takes carry unique descriptive names, so this is a known,
accepted limitation, not a v1 blocker."""
from __future__ import annotations

import pathlib
import traceback
from typing import List, Optional

from tools.media_worker.blob_http import blob_list
from tools.media_worker.job import run_job

DEFAULT_QUEUE_PREFIX = "render_queue/"


def list_queue(queue_prefix: str, token: str) -> List[str]:
    """Blob pathnames of the .mp4 takes under `queue_prefix`, sorted for a
    stable, deterministic pick order across poll cycles."""
    blobs = blob_list(queue_prefix, token)
    return sorted(b["pathname"] for b in blobs
                  if b["pathname"].lower().endswith(".mp4"))


def rendered_stems(out_prefix: str, token: str) -> set:
    """Stems that already have a master `<out_prefix><stem>.mp4` on Blob. One
    list of the output prefix, so pick_next diffs against the whole set rather
    than probing per take."""
    blobs = blob_list(out_prefix, token)
    return {pathlib.Path(b["pathname"]).stem
            for b in blobs if b["pathname"].lower().endswith(".mp4")}


def brand_of(take_pathname: str, queue_prefix: str) -> Optional[str]:
    """Brand = the first path segment under the queue prefix
    (`render_queue/aipg/foo.mp4` -> "aipg"). None if the take sits directly
    under the prefix, letting run_job apply its own BRAND default."""
    rest = (take_pathname[len(queue_prefix):]
            if take_pathname.startswith(queue_prefix) else take_pathname)
    return rest.split("/", 1)[0] if "/" in rest else None


def pick_next(queue_prefix: str, out_prefix: str, token: str, limit: int) -> List[str]:
    """Up to `limit` queued takes whose master does not yet exist on Blob."""
    done = rendered_stems(out_prefix, token)
    eligible = [t for t in list_queue(queue_prefix, token)
                if pathlib.Path(t).stem not in done]
    return eligible[:limit]


def poll_once(env: dict) -> dict:
    """List the queue, render up to POLL_MAX_PER_CYCLE unrendered takes via
    run_job (brand parsed from the path), and return a summary. Guarded per
    take: one take's render failing never sinks the others (the master-exists
    check re-picks it next cycle). Never raises."""
    token = env["BLOB_READ_WRITE_TOKEN"]
    queue_prefix = env.get("RENDER_QUEUE_PREFIX", DEFAULT_QUEUE_PREFIX)
    out_prefix = env.get("OUT_PREFIX", "renders_th/")
    limit = int(env.get("POLL_MAX_PER_CYCLE", "1"))

    takes = pick_next(queue_prefix, out_prefix, token, limit)
    rendered = []
    for take in takes:
        try:
            job_env = dict(env)
            job_env["TAKE_PATHNAME"] = take
            brand = brand_of(take, queue_prefix)
            if brand:
                job_env["BRAND"] = brand
                job_env.pop("EDIT_JSON", None)  # brand default; no stale per-process edit
            urls = run_job(job_env)
            rendered.append({"take": take, "master_url": urls.get("master_url")})
            print(f"[poll] rendered {take} -> {urls.get('master_url')}", flush=True)
        except Exception:
            print(f"[poll] render FAILED for {take}", flush=True)
            traceback.print_exc()
    return {"queued": len(takes), "rendered": rendered}
