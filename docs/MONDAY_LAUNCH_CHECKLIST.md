# Monday Launch Checklist

**Launch time: Monday 8:30 AM CST**
**Status: Pre-launch prep — Sunday 4:39 PM**

---

## Tyler (AI Phone Guy → GHL)

### Prep complete
- [x] Rebuilt as trigger-event SDR with Challenger methodology
- [x] ICP guardrails: DFW 380 Corridor, plumbers/HVAC/roofers/dental/PI law
- [x] GHL custom fields created: trigger_event, verified_fact, competitive_insight
- [x] Push code writes to custom fields on contact create
- [x] Custom fields permission added to GHL token
- [x] Nuked 417 initial junk contacts (first cleanup)
- [x] Verified against Aubrey plumber source CSV
- [x] Nuked 178 remaining hallucinations (second cleanup)
- [x] 43 verified real plumbers remain as seed data

### Still required (YOU)
- [ ] **Tyler GHL Workflow** — build 4 email steps from [TYLER_GHL_SEQUENCE.md](TYLER_GHL_SEQUENCE.md)
- [ ] Each step: unique subject, preview text, body with merge tags
- [ ] Booking CTA: `<a href="https://bit.ly/4sMZpTi" target="_blank">Book demo now</a>`
- [ ] Wait times: 3 business days, 2 business days, 3 business days
- [ ] **Publish workflow**
- [ ] **Change trigger to "Record Created"** (was Record Updated for existing contact enrollment — now we want new Tyler prospects to auto-enroll)

### Tomorrow morning (automated)
- Tyler runs at 8:30 AM CST (mon-fri, every 2 hours)
- Finds 3 trigger-event prospects in 380 Corridor
- Writes to GHL with custom fields populated
- Workflow fires on Record Created → enrolls in email sequence
- First email sends immediately
- Steps 2-4 send on 3, 2, 3 business day delays

---

## Marcus (Calling Digital → Attio)

### Already live
- [x] Running Mon-Fri 8:32 AM CST
- [x] 4 Attio workflows live (med-spa, pi-law, real-estate, home-builder)
- [x] 4 sequences published and enrolled (182 contacts)
- [x] Vertical rotation: Mon=med-spa, Tue=pi-law, Wed=real-estate, Thu=home-builder, Fri=med-spa

### Nothing required
- Already running. New prospects auto-enroll via workflows.

---

## Ryan Data (Automotive Intelligence → HubSpot + Instantly)

### Prep complete
- [x] Rebuilt as nationwide (not DFW-only) trigger-event SDR
- [x] ICP: US car dealerships with trigger events
- [x] HubSpot push skips email sending (Instantly handles it)
- [x] HubSpot cleanup: 59 inbox noise deleted, 246 real dealers kept
- [x] Instantly integration built (tools/instantly.py)
- [x] HubSpot push auto-adds leads to Instantly campaign

### Still required (YOU)
- [ ] **Instantly account setup** — connect sending email michael@automotiveintelligence.io
- [ ] **Create Instantly campaign** "Ryan Data — US Dealerships" from [RYAN_DATA_INSTANTLY_CAMPAIGN.md](RYAN_DATA_INSTANTLY_CAMPAIGN.md)
- [ ] 4 steps, delays 0/3/3/4 days, Steps 2-4 with unique subjects (GHL taught us threading doesn't always work)
- [ ] **Launch campaign**
- [ ] Copy Instantly campaign ID → add to Railway as `INSTANTLY_CAMPAIGN_RYAN_DATA`
- [ ] Redeploy

### Tomorrow morning (automated once env var set)
- Ryan Data runs at 8:34 AM CST (every 2 hours)
- Finds 3 trigger-event dealerships nationwide
- Writes to HubSpot (deal + contact)
- Adds lead to Instantly campaign
- Instantly sends 4 emails over 10 days

---

## Critical env vars (verify in Railway)

```
GHL_API_KEY               # pit-...
GHL_LOCATION_ID           # ZoxVB4ibMZZ2lZ5QpXep
GHL_PIPELINE_ID           # 1682KpTn57QmQulK2TKE
GHL_STAGE_NEW_PROSPECT    # a6935c6b-71ae-4a63-8c76-975fd2afeedb
GHL_WORKFLOW_TYLER        # 9f71377d-73d2-4efb-8161-2e7853fc217a
ATTIO_API_KEY             # set
HUBSPOT_ACCESS_TOKEN      # set
INSTANTLY_API_KEY         # NEEDS TO BE ADDED
INSTANTLY_CAMPAIGN_RYAN_DATA  # NEEDS TO BE ADDED after campaign created
TAVILY_API_KEY            # HIT LIMIT — may need upgrade
OPENROUTER_API_KEY        # set
```

---

## Known issues

1. **Tavily API is over usage limit.** Tyler's enrichment step will fail until this is resolved. Options: (a) upgrade Tavily, (b) swap to Brave Search or SerpAPI. Tyler can still prospect without enrichment but emails won't have verified_fact populated.

2. **43 Tyler contacts are enrichment-less** — they came from the cleanup pre-verification. Their custom fields (trigger_event, verified_fact, competitive_insight) will be empty on their first email unless backfilled.

---

## 8 AM Monday checks

1. Check Pit Wall dashboard — all 3 sales agents show "running" status
2. GHL Enrollment History shows new contacts entering workflow
3. Attio Workflows Runs tab shows new enrollments
4. Instantly campaign shows new leads added
5. First emails sent to real addresses, no 555 phones, no placeholder names
