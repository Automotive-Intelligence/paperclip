# Railway Slipstream blog engine (observable, off the laptop)

**Seat:** Build & Tech #2 (Hardening Crew)
**Date:** 2026-07-19
**Status:** Approved direction (TP: "let's do railway" after the claude.ai-routine path proved un-observable and its runs did not execute).
**Pillar:** 1 (always-on) built the hardening way: observable + on-demand-testable + verifiable.

## Why Railway, not cloud routines

The claude.ai-routine pilot failed and, worse, was a black box: no session logs I can read, emails to an M365 inbox my tools cannot see, and API-triggered runs that registered but never executed. An engine I cannot observe or prove is not hardened. Railway gives full log visibility (`railway logs`), on-demand testing (an admin endpoint), the existing APScheduler, and every content/image/social secret already present. That is the entire point of this crew.

## What paperclip's Railway env already has (verified 2026-07-19)

- `ANTHROPIC_API_KEY` (content generation; paperclip already uses `from anthropic import Anthropic`).
- `FAL_KEY` (images, via `tools/fal_image.py`).
- `ZERNIO_API_KEY` (social, via `tools/social_load.py` / the new `/admin/social-load`).
- MISSING: a GitHub push token and a Vercel token. See Prerequisite.

## Architecture (pure Python in paperclip, deterministic + observable)

`services/slipstream_engine.py`, one function per stage, each independently testable:

1. `pick_topic(brand_cfg) -> dict` — read the brand's queue (avo-telemetry via GitHub REST, or a bundled copy), take the first unchecked topic.
2. `generate_post(brand_cfg, topic) -> Post` — one Anthropic Messages API call (checklist v2 + brand voice + the reference-post structure as context) that returns structured MDX: frontmatter, body (AnswerFirst, EntityDefinition, Callout/ConsoleDiagram, PullQuote, links), 3-image prompt list, LinkedIn + X social drafts. Deterministic single-shot, not an agentic loop.
3. `generate_images(post) -> list[Image]` — for the hero + 2-3 in-body prompts, call `tools/fal_image.generate_nano_banana_image` (business_key per brand). Download bytes.
4. `validate(post, images) -> [violations]` — the GATE, in Python: frontmatter parses; >=1 hero + >=2 in-body images present + referenced; NO em-dashes; ConsoleDiagram steps is a pipe string not an array; required v2 elements present; only-cited StatRow. Any violation HOLDS (no publish).
5. `publish(brand_cfg, post, images) -> PublishResult` — via **GitHub REST** (Git Data API: one commit on a new branch with the MDX + image blobs), then open a **PR**. Vercel auto-builds the PR preview = the build-gate. (Auto-merge to main is a later increment, gated on the Vercel preview build succeeding.)
6. `verify(publish_result) -> bool` — poll the PR's Vercel preview deployment (or, post-merge, the live URL) for success/200.
7. `distribute_social(post, live_url) -> result` — build the jobs array, call the existing `run_social_load` (UTMs + registry + Zernio). Best-effort; never fails the post.
8. `run_brand(brand_key) -> Receipt` — orchestrates 1-7 with gates + a receipt (committed to avo-telemetry via REST). Returns a structured receipt.

**Exposure:**
- `POST /admin/run-slipstream/{brand_key}` (validate_key) — run one brand ON DEMAND. This is the whole reliability win: I can fire it, watch `railway logs`, and verify the PR. Testable.
- APScheduler job `slipstream_engine_mwf` (Mon/Wed/Fri) once proven, iterating over the configured brands.

**Config:** `config/slipstream_brands.yaml` (brand_key -> {repo, blog_dir, business_key, money_pages, voice_ref, queue_path, reference_post}). AvI, AIPG, BAE (all Next.js/Vercel). WD blocked (no blog subsystem). Config-driven per the productization north star.

## Prerequisite (your hands, at the publish boundary only)

A GitHub token with `contents:write` + `pull_requests:write` on `salesdroid/automotive-intelligence`, `ai-phone-guy-site`, `buildagentempire`, and `avo-telemetry` (for receipts). Least privilege = a **fine-grained PAT**, which is a GitHub UI action. Set it in Railway as `SLIPSTREAM_GH_TOKEN`. Everything up to `publish()` builds + tests without it.

## Increment plan (each provable)

1. `generate_post` (Anthropic API) + `validate` gate. TDD. Provable: run it, inspect the MDX + violations locally.
2. `generate_images` (fal) + wire into a full `Post`. Provable: real image URLs.
3. `publish` via GitHub REST as a branch + PR. Needs the PAT. Provable: a real PR with the post + images + Vercel preview.
4. `POST /admin/run-slipstream/{brand}` on-demand endpoint. Provable: fire it, watch logs, see the PR.
5. Verify + `distribute_social` + receipt. Provable end-to-end.
6. Auto-merge (gated on Vercel preview build success) + APScheduler MWF + pause the brand's laptop lane. Then replicate AIPG/BAE, retire the laptop cron.

## Definition of done

A full-Slipstream, image-rich post is produced, gated, and published by the Railway engine, fired on-demand and verified in `railway logs` + the live PR/URL, with the laptop lane paused. Then it runs on schedule for AvI/AIPG/BAE and the laptop cron is retired. WD flagged (blog subsystem does not exist yet).

## Non-goals

- No `npm build` in the container (Vercel is the build environment; the PR preview is the build-gate).
- No `claude -p` / agentic loop in the container (deterministic single-shot generation = observable + testable; avoids the opacity that sank the routine path).
- No broad GitHub token (fine-grained PAT only).
