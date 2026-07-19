# Web-based Slipstream blog engine (all brands, off the laptop)

**Seat:** Build & Tech #2 (Hardening Crew)
**Date:** 2026-07-19
**Status:** Approved direction (TP: "migrate all to a web-based Slipstream engine we created"). Pilot on AvI.
**Pillar:** 1 (always-on) with 3 (verification) preserved.

## Problem

The full Slipstream blog engine (blog + fal hero/in-body images + Zernio social + auto-publish, all gates) runs on Michael's laptop via launchd. Lid closed = no content. The existing claude.ai cloud routines are web-based and always-on but produce a THIN product (component-only, no generated images, PR-only, social drafts) because the routine cloud env cannot hold the API secrets the rich steps need (FAL_KEY, ZERNIO_API_KEY). Paperclip (Railway) holds every one of those secrets. So the two halves exist; they are just not connected.

## Goal

A web-based, always-on engine that produces FULL Slipstream posts (image-rich, auto-published, socially distributed, all gates) for every brand, replacing the laptop cron. Verified by: a full-Slipstream post goes live with the laptop closed, proven end to end.

## Architecture: routine authors + publishes, paperclip serves the secret-holding steps

- **The claude.ai routine (web-based, per brand)** stays the engine shell: it clones the brand repo + avo-telemetry, authors the MDX post to the file-98 standard, runs the gates + `npm run build`, commits, and publishes. It already does git push (proven: AvI PRs #22/#25). No API secrets needed in the routine env.
- **Paperclip (Railway, holds all secrets)** serves the two rich steps the routine cannot do itself:
  - **Images:** NEW `POST /admin/blog-image` wraps `tools/fal_image.py::generate_nano_banana_image` and returns fal image URL(s). The routine calls it (authed), downloads the URL(s) into the brand repo's `public/blog/`, sets `heroImage` frontmatter + in-body `<img>`. This makes cloud posts image-rich (Slipstream requires hero + 2-3 in-body, zero-image = auto-HOLD).
  - **Social:** REUSE the existing `POST /content/publish/zernio/{business_key}` + `/social/zernio/draft` endpoints for distribution. The routine posts its social pack to paperclip, which distributes via Zernio with UTMs + registry (the loader already lives here).
- **Autonomy:** the routine flips from PR-only to **blog-only auto-publish** (merge to main, Vercel deploys), under the same scoped authorization Michael granted the laptop engine 2026-07-02 and the TP extended to the cloud routines 2026-07-19. Every gate stays: build-gate, Iris visual gate (new visuals only; the locked component set is pre-gated), locked voice, no-em-dash, hero-metrics, cited-stats. A gate fail HOLDS (opens a PR + flags CMO) exactly as today.

## Non-goals (YAGNI)

- Not a Railway container running `claude -p` (heavier; the routine already runs Claude Code in the cloud). Reuse the routine.
- Not provisioning arbitrary secrets into the routine cloud env (unavailable/limited; that is the whole reason for the paperclip-delegation design).
- Not changing the file-98 content standard, the gates, or the social grid logic. Reuse.

## Scope of THIS increment (the pilot)

1. **Build `POST /admin/blog-image`** in paperclip (wrap `fal_image.py`), authed, returns `{urls, model, aspect_ratio}`. TDD.
2. **Upgrade the AvI routine** (`trig_01A8Q9piSacZYZiLFcyMhFRt`) prompt: add the image step (call `/admin/blog-image` for hero + 2-3 in-body, download + commit, set frontmatter), keep the file-98 body, flip SHIP from PR-only to blog-only auto-publish (merge to main after green build + gates), add the social step (POST the pack to the Zernio endpoint). Preserve the Gmail fail-safe.
3. **Pause the laptop AvI lane** (edit `avo-telemetry/scripts/blog_engine_prompt.md` to exclude AvI) so the two engines can never double-publish. The laptop keeps AIPG/BAE/WD until they migrate.
4. **Prove it:** run the routine (`RemoteTrigger run`), then verify a live AvI post: HTTP 200, hero + >=2 in-body images rendering, 0 em-dash in rendered HTML, schema present, and a social pack distributed. Record the receipt.

## Definition of done (pilot)

A full-Slipstream AvI post (image-rich, gated, auto-published, socially distributed) goes live from the cloud routine with the laptop's AvI lane paused, verified by live probe. Then this pattern replicates per brand (AIPG, BAE, WD) and the laptop cron is retired.

## Endpoint contract (Task 1)

```
POST /admin/blog-image
Authorization: Bearer <API_KEYS>
body: {
  "prompt": str,                 # required, the visual description
  "business_key": str,           # brand style prefix (e.g. "autointelligence")
  "aspect_ratio": str = "",      # "16:9" hero, "1:1" etc; empty = platform default
  "pro": bool = false,           # Pro for hero finals
  "reference_image_urls": [str]  # optional, brand refs for consistency
}
-> 200 {"ok": true, "urls": [str], "model": str, "aspect_ratio": str}
-> 200 {"ok": false, "error": str}    # fal error surfaced, never a 500
```

## Testing (Task 1)

`tests/test_blog_image_endpoint.py` (mock `generate_nano_banana_image`):
1. valid body + tool returns a dict -> 200 `{ok:true, urls:[...]}`.
2. tool returns an error string (FAL_KEY missing / fal error) -> 200 `{ok:false, error:...}`.
3. missing `prompt` -> 422/400.
4. no auth -> 401 (validate_key).

## Risks + mitigations

- **Double-publish during cutover:** mitigated by pausing the laptop AvI lane BEFORE the routine auto-publishes (step 3 before step 4).
- **Cloud routine cannot download the fal URL:** the routine env has Bash + curl; fal URLs are public CDN links. Verify in the pilot run.
- **Image quality/Iris gate:** new visuals route to Iris per governance; the routine uses the brand's locked style prefix (BRAND_PROMPT_STYLES) so images inherit the approved look. Zero-image still auto-HOLDs.
- **Revenue engine:** AvI already has fresh content through 07-18; the brief pause during cutover costs nothing.
