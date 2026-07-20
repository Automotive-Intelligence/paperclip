# Media Worker (video cloud-worker) deploy runbook

Status 2026-07-20. The render pipeline is PROVEN in the deployable image (a real 6s
AIPG take rendered end to end inside `avo-media-worker` on linux/amd64: whisper ->
cut_talking_head -> caption -> end card -> contact sheet, valid 1080x1920 h264 master).
This runbook is the remaining path to the LAPTOP-OFF TEST. Steps marked [MICHAEL] need
the owner; [BUILD] is code work an agent can do once unblocked.

## What is proven (this branch, `bt4/media-worker`)
- Image `Dockerfile.media-worker` builds on linux/amd64 (Railway target), 364MB, no model baked.
- Toolchain works in-image: ffmpeg 5.1.9, whisper.cpp 1.9.1 (static), PIL variable fonts.
- `smoke.py` runs each binary (catches loader failures a `which` check misses).
- `tools/media_worker/`: transcribe, edit.json->argv, dedup Blob sync, render orchestration (all unit-tested).
- `services/media_worker.py` `run_video()` stages a render (stage-and-flag only, never schedules).
- Real in-container render receipt: master + contact sheet produced from a real take via mounted assets.

## The one real gap the render proof surfaced: ASSET PATHS
The pipeline scripts read ZERO env vars for paths. `cut_talking_head.py` hardcodes:
- fonts at `HOME/avo-telemetry/assets/fonts/` (NOT the image's `/opt/media/fonts`)
- brand logos at `HOME/<brand>-site/public/...` and `HOME/avo-telemetry/assets/brand/...`
The in-container proof worked by MOUNTING those at the container HOME (`/root/...`). For a
mount-free deploy, [BUILD] one of:
- **(A, recommended) stage assets into the HOME layout.** Bake the 4 fonts at
  `/root/avo-telemetry/assets/fonts/`, and boot-pull the brand logos to their HOME-relative
  paths. Zero script changes (honors "port, do not rewrite"). Concretely: add
  `COPY assets/fonts/ /root/avo-telemetry/assets/fonts/` to the Dockerfile, and have the
  boot-pull place `<brand>-site/public/*` + `avo-telemetry/assets/brand/*` under `/root`.
- **(B) make the scripts env-driven** (FONTS_DIR / BRAND_ASSETS). More correct long-term but
  edits the ported scripts; defer unless the Studio wants it.

## Maintainability: vendored scripts can drift
`scripts/{cut_talking_head,build_short,video_review_sheet,stock_fetch}.py` are COPIES of
`~/avo-telemetry/scripts/*.py` staged into the build context. Only `video_review_sheet.py`
was intentionally edited here (the LABEL_FONT path). These copies can drift from the canonical
avo-telemetry versions the Studio maintains. Also: the canonical `video_review_sheet.py` still
has the Mac-only Arial path and should get the same LABEL_FONT fix to stay in sync. Longer term,
decide vendor-vs-submodule (raised in the plan) so there is one source of truth.

## Deploy steps
1. [MICHAEL] Blob token: `cd ~/worship-digital-site && vercel env pull` -> `BLOB_READ_WRITE_TOKEN`,
   then into Doppler -> Railway (nothing pasted). (Token is NOT currently in a local .env.)
2. [BUILD] Boot-pull script: on container start, pull from Blob into the cache + HOME layout:
   - `ggml-small.en.bin` -> `/opt/media/cache/whisper/` (WHISPER_MODEL)
   - gated stock -> `STOCK_LIB`
   - brand logos -> the HOME-relative paths (gap A above)
   Dedup via `tools/media_worker/blob_sync.py` (already handles the random-suffix gotcha).
3. [BUILD] Trigger: add `POST /admin/run-video` (mirror the proven `POST /admin/run-watchdog`,
   paperclip PR #161) that calls `services.media_worker.run_video(take, edit)`; then wire the
   deferred stage steps in `run_video` (Blob push of master+sheet, REVIEW_LOG append, CMO flag).
4. [MICHAEL] Railway service: new service from `Dockerfile.media-worker` (Railway builds amd64
   natively; the arm64 NEON issue only affects local Apple-Silicon builds). Confirm service
   creation + cost. Healthcheck green.
5. [MICHAEL + BUILD] LAPTOP-OFF PROOF: with the Mac shut, trigger the service for one AIPG take;
   receipts = Railway logs, master+sheet objects on Blob (byte counts), CMO flag raised, zero
   auto-schedule. This is the acceptance.

## Non-negotiables the deploy must preserve
Real-take VO only; file-133 + file-117 gates with visible receipts before anything ships; CMO
go before scheduling; stage-and-flag, never silent auto-fire; Book'd = Ryan; no fabricated stats;
no em-dashes. The X-280 guard (Task 1) is already in the loader.

## Fast-follows (not blockers)
build_short clone-voice path; stock_fetch port; Blob-poll auto-trigger; the licensed music bed
(build in the cloud version, never locally first).
