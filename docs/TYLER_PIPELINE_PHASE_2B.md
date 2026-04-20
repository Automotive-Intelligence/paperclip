# Tyler Pipeline — Phase 2b Hand-Off Checklist

**Date written:** 2026-04-19
**Last updated:** 2026-04-19 (webhook deployed + smoke-tested)
**Owner:** Michael
**Status:** **95% done.** Code, env vars, and stage all live. Only remaining step: configure Instantly's reply webhook to POST to our endpoint.

## What Phase 2b does

Closes the contacts-only architecture loop: when a Tyler cold-email recipient **replies** in Instantly, an Opportunity is created in GHL at $482 in the Intent Shown stage so the pipeline only contains intent-qualified leads. Until this final step is configured in Instantly, replies will not auto-promote.

## Current state (as of this update)

| Layer | State |
|---|---|
| Tyler cold push | ✅ Contact only (tagged `tyler-prospect` + `cold-email` + `{industry}`). No Opportunity created. |
| GHL Lead Pipeline | ✅ 19 backfilled opps at $482 in `Requested 📝`. New replied-opps will land in `Contacted ✉️ᯓ➤`. |
| GHL `pit-…` token | ✅ Has `opportunities.readonly` + `opportunities.write` scopes. |
| `/webhooks/instantly` route | ✅ Live at `https://paperclip-production-ba14.up.railway.app/webhooks/instantly` (commit `6b93f22`). |
| `INSTANTLY_WEBHOOK_SECRET` | ✅ Set in Railway. Value is a 32-byte URL-safe random token (only Michael can read it via `railway variables --kv`). |
| `GHL_STAGE_INTENT_SHOWN` | ✅ Set to `b734ba61-f185-48c6-8e8e-8bb4084c3a4d` (the existing empty `Contacted ✉️ᯓ➤` stage). |
| Instantly webhook config | ❌ **Not wired yet — final manual step (5 min).** |

## Stage choice

Used the existing **`Contacted ✉️ᯓ➤`** stage instead of creating a new one. Rationale: stage was empty, name semantically fits "we're in conversation now," and avoids GHL UI work. To re-route to a different stage later, change the `GHL_STAGE_INTENT_SHOWN` Railway env var only — no code change.

If you'd rather have a clearly-named "Intent Shown" stage, create it in GHL UI and update the env var. The webhook code doesn't care which stage id it points at.

## Smoke-test results (2026-04-19)

| Case | Expected | Actual |
|---|---|---|
| No `X-Instantly-Secret` header | 401 unauthorized | ✅ 401 |
| Wrong secret | 401 unauthorized | ✅ 401 |
| Correct secret + `email_opened` event | 200 ignored | ✅ 200 ignored |
| Correct secret + reply for unknown email | 200 contact_not_found | ✅ 200 contact_not_found |

Auth and dispatch logic verified live. Happy-path (real reply → opp created) intentionally not smoke-tested to avoid creating a synthetic opp; it will activate naturally on the first real reply once Instantly is wired.

## The one remaining step — configure Instantly

1. Pull the secret value:
   ```sh
   railway variables --kv | grep ^INSTANTLY_WEBHOOK_SECRET=
   ```
2. Instantly → **Settings → Webhooks** (or the campaign-level webhook config for campaign `cc56b15a-e148-4f64-a9dd-fd06b4b4b479`).
3. Add a webhook:
   - **URL**: `https://paperclip-production-ba14.up.railway.app/webhooks/instantly`
   - **Event**: `reply_received` (Instantly's exact event name may vary — see "Event-name tolerance" below)
   - **Custom header**: `X-Instantly-Secret: <secret-from-step-1>`
4. Save.
5. Send a real reply to a Tyler email from a personal inbox to test end-to-end.
6. Verify in GHL: contact gets `replied` + `intent-shown` tags AND a new $482 opp exists in `Contacted ✉️ᯓ➤` stage.

## Event-name tolerance built into the handler

The handler matches **any event whose name contains "reply"** (case-insensitive). It also accepts payload keys `event_type` or `event`, and email under `lead_email` or `email`. So Instantly's exact field naming doesn't need to match a single string — anything reply-shaped will trigger the promotion.

## Backfill of already-replied contacts

If there are GHL contacts already tagged `replied` from prior workflow activity but no Opportunity, write a one-off script following the [tools/ghl_backfill.py](../tools/ghl_backfill.py) pattern (dry-run default, `--execute` writes). Estimated count: low double-digits at most. Optional polish — not blocking.

## Rollback

- **Webhook misbehaving:** unset `INSTANTLY_WEBHOOK_SECRET` in Railway → all Instantly POSTs will be silently ignored (since handler requires the header to match a non-empty secret). Or remove the webhook from Instantly's UI.
- **Wrong stage:** change `GHL_STAGE_INTENT_SHOWN` Railway env var to a different stage id; takes effect on next webhook fire (no redeploy).
- **Want to re-enable cold-push opp creation:** revert commit `0805cb0` (Phase 2a). All other phases remain compatible.

## Out of scope for Phase 2b

- GHL stage automation (e.g. auto-move `Contacted ✉️ᯓ➤` → `CLOSED 💰` on calendar event). Separate effort.
- Won/lost tracking via GHL webhook — already wired at `/webhooks/ghl`, no changes needed.
- Instantly `email_opened` as soft intent signal. Start with reply only; add opens later if reply-only feels too narrow.
