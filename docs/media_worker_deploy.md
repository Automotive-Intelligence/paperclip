# Media Worker (video cloud-worker) deploy runbook

Status 2026-07-22. **SHIPPED + proven in the cloud.** Video production runs off the laptop:
the deployed Railway service (not `railway run`, not the Mac) pulls a raw take + the whisper
model from Vercel Blob, renders, and stages the master + review sheet back to Blob. Two AIPG
desk-takes rendered this way end to end (h264 1080x1920, word-lit captions, contact sheet),
including a re-render that verified the Blob overwrite path.

## Architecture (what actually runs)
- **Image** `Dockerfile.media-worker` (linux/amd64, Railway target): ffmpeg + whisper.cpp v1.9.1
  (static) + PIL, the 4 pipeline scripts, and brand fonts/logos baked at the HOME-relative paths
  the scripts read (`/root/avo-telemetry/assets/...`, `/root/<brand>-site/public/...`). No model
  baked; no mounts needed to render.
- **Trigger** `tools/media_worker/asgi.py` — raw ASGI app. `POST /run-video` (authenticated) is
  the render trigger; `GET /health` (and `/`) is the healthcheck; everything else 404s.
- **Job** `tools/media_worker/job.py` `run_job(env)` — pulls the model (skip-if-local) + take from
  Blob, renders via `render_one` (`transcribe -> cut_talking_head -> contact sheet`), pushes the
  master + sheet, appends a `renders_th/REVIEW_LOG.md` stage-flag entry. Stage-and-flag ONLY:
  never schedules or publishes.
- **Blob I/O** `tools/media_worker/blob_http.py` — pure HTTP (no vercel CLI / Node). Private-store
  PUT uses `x-vercel-blob-access: private` + `x-add-random-suffix: 0` + `x-allow-overwrite: 1`
  (a re-put of the same pathname replaces, not duplicates). LIST paginates; DOWNLOAD streams with
  the mandatory Bearer header.

### Railway startCommand quirk (why the image looks the way it does)
Railway applies paperclip's `railway.toml` `uvicorn app:app --host 0.0.0.0 --port $PORT`
startCommand to THIS service too, and does NOT shell-expand `$PORT`. The image makes that exact
command valid: uvicorn is installed, `/app/app.py` is the media-worker ASGI app, and a uvicorn
wrapper (`/usr/local/bin/uvicorn`) binds the real `${PORT}` via `python -m uvicorn`.

## Single-source rule for the 4 pipeline scripts (TP ruling 2026-07-20)
`scripts/{cut_talking_head,build_short,video_review_sheet,stock_fetch}.py` are baked into the
image and are the PRODUCTION producer. RULING: **paperclip/scripts/ is canonical; no forking.**
The `~/avo-telemetry/scripts/` copies carry a `# MIRROR ONLY` header. The cloud end-to-end AIPG
proof is DONE, so the avo-telemetry copies are now cleared to RETIRE (see checklist).

### Merge checklist
- [x] The 4 scripts are single-source in paperclip; no forking. The avo-telemetry copies are MIRROR ONLY.
- [x] First cloud end-to-end AIPG render proven -> retire `~/avo-telemetry/scripts/{cut_talking_head,build_short,video_review_sheet,stock_fetch}.py`.
- [ ] `VIDEO_ROUTINE_TOKEN` is set on the Railway media-worker service (REQUIRED). Without it,
      `POST /run-video` fails closed (401) and no render can be triggered, yet `/health` still
      reads 200 -- the worker looks healthy but is untriggerable. Not optional.
- [x] `BLOB_READ_WRITE_TOKEN` set on the service (Blob list/download/put).

## Deploy steps
1. [MICHAEL] Railway auth: `railway login` (interactive) OR set a durable `RAILWAY_API_TOKEN` in
   Doppler for non-interactive redeploys (currently the Doppler value is invalid -- flagged).
2. [BUILD] `railway up . --service media-worker --path-as-root --ci` from the repo root. Railway
   builds amd64 natively (the arm64 NEON issue only affects local Apple-Silicon builds).
3. [MICHAEL] Confirm the service has a generated domain (`railway domain`) so port/health
   detection works, and set `VIDEO_ROUTINE_TOKEN` + `BLOB_READ_WRITE_TOKEN`.
4. Trigger a render:
   `curl -X POST https://<service>/run-video -H "authorization: Bearer $VIDEO_ROUTINE_TOKEN"
   -H "content-type: application/json" -d '{"take":"<blob pathname>","edit":{"brand":"aipg"}}'`
   Response: `{"status":"staged","master_url":...,"sheet_url":...}`. Receipts: Railway logs, the
   master+sheet objects on Blob, a new `renders_th/REVIEW_LOG.md` entry stamped `NOT scheduled`.
5. Watchdog: `services/watchdog.py` health-checks the service (config `media_worker.health_url`,
   critical severity, GitHub-issue alert rail).

## Auto-trigger (Blob-poll) -- doc-142 fast-follow
So the worker is not only manual `POST /run-video`: a take dropped under the Blob render queue
gets rendered + staged automatically.
- **Queue:** upload a take to `render_queue/<brand>/<name>.mp4` on Blob (the `<brand>` folder sets
  the render brand). This is the explicit "please render this" signal; nothing else auto-renders.
- **`POST /poll`** (authenticated, same `VIDEO_ROUTINE_TOKEN`): renders up to `POLL_MAX_PER_CYCLE`
  (default 1) unrendered queued takes in a background thread, returns immediately. Idempotent: a
  take whose master `renders_th/<stem>.mp4` already exists is skipped. A shared render lock means
  `/poll` and `/run-video` never run two renders at once (the second gets `busy`).
- **Clock:** the paperclip scheduler POSTs `${MEDIA_WORKER_URL}/poll` every 15 min (job
  `media_worker_poll`). **Inert until `MEDIA_WORKER_URL` is set on the paperclip service** -- set it
  (+ `VIDEO_ROUTINE_TOKEN`) to activate the auto-trigger; leave unset to keep manual-only.
- **Config (worker):** `RENDER_QUEUE_PREFIX` (default `render_queue/`), `POLL_MAX_PER_CYCLE`
  (default 1). Stage-and-flag preserved: auto-renders stage + flag, never publish.

## Non-negotiables the deploy preserves
Real-take VO only; file-133 + file-117 gates with visible receipts before anything ships; CMO go
before scheduling; stage-and-flag, never silent auto-fire; Book'd = Ryan; no fabricated stats;
no em-dashes.

## Fast-follows (not blockers)
File-117 no-face AIPG finish (VO-over-b-roll via `build_short` + a Studio `broll_at` edit spec --
flagged); canonical brand logos from Iris (the baked marks are placeholders -- flagged);
durable `RAILWAY_API_TOKEN` for hands-free redeploy (flagged); Blob-poll auto-trigger.
