# Blob-poll auto-trigger — design

**Goal:** Make the cloud media worker render new takes WITHOUT a manual `POST /run-video`. A take
dropped on Blob gets rendered + staged automatically, on a schedule, preserving stage-and-flag.

**Context:** doc-142 fast-follow. The worker (PR #203, merged) renders + stages a take on demand via
authenticated `POST /run-video`. Today a human must fire that. This adds the automatic clock.

## Decisions (decide-and-inform; reversible; within B&T mandate)

1. **Poll logic lives ON the media-worker**, not paperclip. The worker already owns `blob_http` +
   `run_job`; keeping listing+render there avoids duplicating Blob logic and keeps paperclip as a
   thin clock. New: `tools/media_worker/poller.py` + an authenticated `POST /poll` in `asgi.py`.

2. **The clock is a paperclip APScheduler job** that POSTs `media-worker/poll` on a cadence,
   reusing the proven pattern (`scheduler.add_job(..., CronTrigger(...))`, same as the watchdog).
   No new Railway cron service (Railway cron replaces a web service's start command — wrong shape
   for a service that must also serve `/run-video`). Fire-and-forget: paperclip does not wait for
   the render.

3. **Eligibility = explicit opt-in via a queue prefix.** The poller lists `RENDER_QUEUE_PREFIX`
   (default `render_queue/`) and renders takes under `render_queue/<brand>/<name>.mp4`. Dropping a
   take there is the "please render this" signal. Default opt-in (not "render everything under
   raw_shoot/") so a raw shoot's alternate/blooper takes never auto-render. The prefix is
   configurable, so it CAN be pointed at `raw_shoot_` if that is ever wanted (YAGNI: default safe).

4. **Idempotency = master-exists on Blob.** A take is "done" if `renders_th/<stem>.mp4` already
   exists. No separate ledger (the master IS the receipt). `x-allow-overwrite` means a rare double
   render is harmless (it replaces).

5. **Bounded work + single-render lock.** `/poll` renders at most `POLL_MAX_PER_CYCLE` (default 1)
   eligible take per call, in a background thread (reuse asgi's `_kickoff` pattern), and returns
   immediately (`{"status":"polling","queued":N,"rendering":stem|null}`). A module-level render
   lock serializes renders: if a render (manual `/run-video` or a prior poll) is in flight, `/poll`
   reports `busy` and renders nothing. Idempotent + bounded means the queue drains over successive
   cycles without ever running two renders at once (CPU/disk safe on one container).

6. **Stage-and-flag preserved.** Each auto-render is a normal `run_job` call: render → push master +
   sheet → append REVIEW_LOG `NOT scheduled`. The poller NEVER publishes or schedules outward. It
   only decides WHICH take to render, not whether it ships — the file-133/117 + CMO gate is
   untouched.

## Components
- `tools/media_worker/poller.py`
  - `list_queue(prefix, token) -> [take_pathname,...]` — Blob list under the queue prefix, `.mp4` only.
  - `is_rendered(take_pathname, out_prefix, token) -> bool` — does `renders_th/<stem>.mp4` exist?
  - `pick_next(prefix, out_prefix, token, limit) -> [take,...]` — eligible (unrendered), capped at `limit`.
  - `poll_once(env) -> dict` — pick_next, render up to `limit` via `run_job` (per-take env overlay,
    brand parsed from `render_queue/<brand>/`), return a summary. Guarded per take.
- `tools/media_worker/asgi.py`
  - `POST /poll` — auth via `VIDEO_ROUTINE_TOKEN` (same as `/run-video`); acquires the render lock;
    if busy → `{"status":"busy"}`; else spawns a background thread running `poll_once` and returns
    `{"status":"polling","queued":N}`. Fail-closed like `/run-video`.
  - Generalize the existing startup `_lock`/`_kickoff` into a shared render lock both paths respect.
- paperclip `app.py`
  - `scheduler.add_job(_poll_media_worker, CronTrigger(minute="*/15", timezone=CST), id="media_worker_poll", ...)`
    — a thin function that POSTs `${MEDIA_WORKER_URL}/poll` with `VIDEO_ROUTINE_TOKEN`, short timeout,
    swallows errors (the worker owns the work; a missed tick is picked up next cycle).

## Config / env
- Worker: `RENDER_QUEUE_PREFIX` (default `render_queue/`), `POLL_MAX_PER_CYCLE` (default 1),
  plus the existing `BLOB_READ_WRITE_TOKEN`, `VIDEO_ROUTINE_TOKEN`, `OUT_PREFIX`.
- paperclip: `MEDIA_WORKER_URL` (the worker's base URL), `VIDEO_ROUTINE_TOKEN`. If `MEDIA_WORKER_URL`
  is unset, the scheduler job no-ops (so this is inert until configured — safe to merge).

## Error handling
- Poll never raises out of `/poll`: a listing error → `{"status":"error"}` logged, HTTP 200 (the
  clock keeps ticking). A single take's render failure is caught per-take and logged; other takes
  are unaffected; the master-exists check means a retried take re-renders next cycle.
- The paperclip clock swallows connection errors (worker down is already a watchdog anomaly).

## Testing
- `poller.py`: list filters to `.mp4`; `is_rendered` true/false vs a stubbed Blob list; `pick_next`
  skips rendered + respects `limit`; `poll_once` renders the right takes (monkeypatched `run_job`),
  parses brand from the path, and is guarded (one take raising does not sink the others).
- `asgi.py`: `POST /poll` 401 without token; `busy` when the render lock is held; `polling` + spawns
  work when free (monkeypatch `poll_once`); GET/unknown still 404.
- No live Blob/render in unit tests (all seams monkeypatched), matching the existing suite.

## Out of scope (YAGNI)
No manifest/edit-spec-per-take (that arrives with the Studio broll_at work), no priority ordering,
no parallel renders, no dead-letter queue. One take per tick, drain over time, master-exists dedup.
