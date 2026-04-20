# Tyler Pipeline — Phase 2b Hand-Off Checklist

**Date written:** 2026-04-19
**Owner:** Michael
**Status:** Ready to execute. Phases 1 + 2a + 3 already shipped. This doc finishes the architecture.

## What Phase 2b does

Wires the missing half of the contacts-only architecture: when a Tyler cold-email recipient **replies** in Instantly, an Opportunity is created in GHL at $482 in a new "Intent Shown" stage. Until this is done, no new Opportunities are created at all (Phase 2a removed cold-push opp creation).

## Current state after Phases 1, 2a, 3

| Layer | State |
|---|---|
| Tyler cold push | Creates Contact only (tagged `tyler-prospect` + `cold-email` + `{industry}`). No Opportunity. |
| GHL Lead Pipeline | 19 backfilled opps at $482. New opps not being created. |
| Instantly | Sending Tyler's cadence as before. Reply detection not wired into paperclip. |
| GHL `pit-…` token | Has `opportunities.readonly` + `opportunities.write` scopes (added 2026-04-19). |

## Step 1 — GHL UI: create the new pipeline stage

1. GHL → **Opportunities** → **Pipelines** → open **Lead Pipeline**.
2. Add a stage named `Intent Shown` and place it **first** (before `Requested 📝`).
3. Save.
4. Copy the new stage's id from the URL or stage settings.
5. Set Railway env var: `GHL_STAGE_INTENT_SHOWN=<that id>` (env: production, service: paperclip).

(Optional cleanup: rename `Requested 📝` → `Old / Pre-2b` so it's clear it's legacy. Don't delete it — the 19 backfilled opps live there.)

## Step 2 — Instantly: configure the reply webhook

1. Instantly → Settings → **Webhooks** (or the campaign-level webhook config).
2. Add a webhook:
   - URL: `https://<paperclip-railway-domain>/webhooks/instantly`
   - Events: `reply_received` (and optionally `email_opened` if we want open-3x as a softer intent signal — start with reply only)
   - Auth: shared secret. Pick a strong random string. Set it as Railway env var `INSTANTLY_WEBHOOK_SECRET=<value>` and configure Instantly to send it as a header `X-Instantly-Secret`.
3. Save.

## Step 3 — Code: add the webhook handler

Add to `app.py` next to the existing `/webhooks/ghl` handler around [app.py:5184](app.py#L5184):

```python
@app.post("/webhooks/instantly")
async def instantly_webhook(payload: dict, request: Request):
    """
    Instantly fires this on lead reply. Promotes the GHL contact to an
    Opportunity at that point so the pipeline only contains intent-qualified leads.
    """
    secret = os.getenv("INSTANTLY_WEBHOOK_SECRET", "").strip()
    sent = request.headers.get("X-Instantly-Secret", "").strip()
    if secret and sent != secret:
        return {"status": "unauthorized"}

    event = (payload.get("event_type") or payload.get("event") or "").lower()
    email = (payload.get("lead_email") or payload.get("email") or "").strip().lower()
    if event != "reply_received" or not email:
        return {"status": "ignored", "event": event}

    from tools.ghl import (
        search_contact, update_contact_tags,
        create_opportunity, get_pipeline_opportunities,
    )

    contact = search_contact(email=email)
    if not contact:
        logging.warning("[instantly_webhook] contact not found for %s", email)
        return {"status": "contact_not_found"}

    contact_id = contact["id"]
    existing_tags = [str(t) for t in (contact.get("tags") or [])]
    if "intent-shown" not in [t.lower() for t in existing_tags]:
        update_contact_tags(contact_id, existing_tags + ["replied", "intent-shown"])

    pipeline_id = os.getenv("GHL_PIPELINE_ID", "").strip()
    stage_id    = os.getenv("GHL_STAGE_INTENT_SHOWN", "").strip()
    if not (pipeline_id and stage_id):
        logging.error("[instantly_webhook] GHL_PIPELINE_ID or GHL_STAGE_INTENT_SHOWN not set")
        return {"status": "config_missing"}

    # Dedup: don't create a second opp if the contact already has one in this pipeline.
    existing = get_pipeline_opportunities(pipeline_id)
    if any((o.get("contactId") or "") == contact_id for o in existing):
        return {"status": "opp_already_exists"}

    industry = (contact.get("companyName") or "Service Business")
    create_opportunity(
        contact_id=contact_id,
        name=f"{contact.get('companyName', 'Unknown')} - {industry}",
        pipeline_id=pipeline_id,
        stage_id=stage_id,
        monetary_value=482,
        source_agent="tyler",
    )
    track_event("email_replied", "tyler", "aiphoneguy", contact_id=contact_id)
    return {"status": "promoted"}
```

`Request` import: `from fastapi import Request` (already imported in app.py if FastAPI's there; otherwise add).

## Step 4 — Test

1. Send a real reply from a personal inbox to a Tyler cold email already in Instantly.
2. Watch Railway logs: `railway logs --service paperclip --follow | grep instantly_webhook`
3. In GHL: confirm the contact got `replied` + `intent-shown` tags AND a new Opportunity exists in `Intent Shown` stage at $482.
4. Send a SECOND reply from the same address to verify dedup → log should show `opp_already_exists`.

## Step 5 — Backfill replies that already happened

If there are contacts already tagged `replied` in GHL (from prior workflow activity) but no Opportunity, run a one-off promote script. Skeleton (don't run blind, audit first):

```python
# tools/promote_replied_contacts.py
# Find contacts tagged 'replied' but with no opp in Lead Pipeline,
# create the opp at $482. Same pattern as ghl_backfill.py — dry-run default.
```

Estimated impact: low double digits at most. Optional polish, not blocker.

## Risk & rollback

- **Risk:** Instantly sends `event_type` strings that don't match `reply_received` exactly. → log every payload first time, adjust the matcher.
- **Rollback:** Delete the `/webhooks/instantly` route and unset the Instantly webhook in their UI. Phase 2a state (no new opps from cold push) remains.

## Out of scope for Phase 2b

- GHL stage automation (e.g. auto-move `Intent Shown` → `Demo Booked` on calendar event). Separate effort.
- Won/lost tracking via GHL webhook — already wired at [app.py:5184](app.py#L5184), no changes needed.
- Instantly `email_opened` as intent signal. Start with reply-only; add opens later if reply-only feels too narrow.

## Estimated time

- GHL UI changes (Step 1): 5 min
- Instantly webhook config (Step 2): 5 min
- Code + commit + deploy (Step 3): 30 min
- End-to-end test (Step 4): 15 min

**Total: ~1 hour. Best done at a desk with both GHL and Instantly tabs open.**
