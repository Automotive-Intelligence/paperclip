# Monday 2026-04-20 Launch Runbook

**What this is:** The first real Monday after a 48-hour sprint that wired up 3 sales agents, 3 email systems, and 3 RevOps pit walls across AI Phone Guy, Automotive Intelligence, and Calling Digital. This runbook walks you through what to check, in what order, from 8:30am through the day.

---

## 8:30am CDT — Pre-flight

**Before the 9am send window opens, verify nothing broke overnight.**

### Check 1: Railway deploy is current
- Open Railway dashboard → paperclip service → Deployments
- Latest deployment should show commit `a69d09c` or newer, "SUCCESS"
- If it says FAILED or BUILDING, wait for it to resolve before anything else

### Check 2: Sender inbox is healthy
- Open Gmail for `info@theaiphoneguy.ai`
- No bounces from Friday's overnight test. No suspensions. No "account locked" warnings.
- Send yourself a test email from the account (not through Instantly). Confirm it delivers.
- If Gmail is fine, sender reputation is fine.

### Check 3: Instantly campaign is still Active
- Open Instantly → AI Phone Guy workspace → "Tyler — Instantly Email Sequence: The AI Phone Guy"
- Status should be **Active** (blue). If it says Paused, hit Resume.
- Options tab: Open Tracking = Enabled, Link Tracking = checked. If these reverted, re-enable and Save.
- Leads tab: ~18 leads, all showing "Waiting" or "In Sequence".

---

## 9:00am CDT — Send window opens

**This is the moment the whole sprint was building toward.** First Step 1 emails should start firing any minute.

### What "good" looks like

By 9:15am, you should see:
- Instantly Analytics tab: "Sequence started" counter climbing (not stuck at 0)
- Step Analytics: Step 1 "Sent" column incrementing 1, 2, 3, 4...
- Sender account dashboard: outbound count rising

By 10:30am:
- Ideally 15-18 of the 18 leads have been sent Step 1
- Some may have opens (first pixel fires within minutes for mobile inboxes)
- Campaign open rate should start showing a real number instead of `-`

By 11:00am (window closes):
- All 18 should be sent OR hitting the daily_limit=30
- Any remainder spills to Tuesday 9am

### What "bad" looks like

- **0 sent by 9:30am** → Something is wrong. See "If nothing fires" below.
- **1-2 sent then stalled** → Likely sender throttle or warm-up ramp. Let it run until 11am before acting.
- **Bounces** → Check which email. If it's one of the generic addresses (info@, service@), that's expected — they filter more aggressively. If it's a named inbox (mitchell@, kecia@, anthonym@), sender reputation needs investigation.

### If nothing fires by 9:30am

**Do NOT pause the campaign or change settings.** Diagnose first.

1. **Is the campaign still Active?** If it silently went back to Draft, that's a sync issue. Re-activate.

2. **Check lead status via API** (use the same pattern from Saturday):
```bash
TYLER_KEY=$(railway variables --json | python3 -c "import sys,json; print(json.load(sys.stdin).get('INSTANTLY_API_KEY_TYLER',''))")
curl -sS -X POST "https://api.instantly.ai/api/v2/leads/list" \
  -H "Authorization: Bearer $TYLER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"campaign":"cc56b15a-e148-4f64-a9dd-fd06b4b4b479","limit":20}'
```
Look for `status`, `email_step_count`, `last_contacted_email_sent_at`. If all 18 still show step=0 and last_contacted=null, the scheduler hasn't fired.

3. **Instantly Diagnose button** — Analytics tab has a "Diagnose" button. Click it. It runs Instantly's built-in health check and will tell you if there's a missing config, failed sender auth, or throttle condition.

4. **Escalate to next Claude session** — paste the symptom + the Diagnose output. Claude reference: the handoff memory at `project_session_handoff_2026_04_19.md` has the full state.

---

## 10:00am CDT — Joshua's first Race Report

**Joshua (pit wall) runs every 2 hours, so his first Monday run lands around 10am.**

- Railway logs: search for `=== RACE REPORT — JOSHUA` (or `[Joshua]` prefix)
- Report should show grid positions for all 18 leads
- Early Monday: most leads should be at `P18 — No opens` or `P12 — Opened once`
- Check GHL contact records: each enrolled lead should have a `pit-p12` or `pit-p18` tag applied

If Joshua's report says "No leads in campaign yet" — that's a sync issue. Check that Instantly's `leads/list` endpoint is returning the same 18 leads Joshua should see.

---

## 11:00am CDT — Window closes, check the real numbers

Open the Instantly Analytics dashboard. You should see real numbers in every column:

| Metric | Expected range |
|--------|---------------|
| Sequence started | 15-18 |
| Open rate | 10-40% (don't panic if low on day 1 — some inboxes don't register open pixel) |
| Click rate | 0-5% (Step 1 has a Book Demo link, not many click it on first touch) |
| Reply rate | 0-10% (realistic first-touch reply rate is 1-3%) |
| Opportunities | 0-1 (anyone who replied positively) |

Save a screenshot. This is your baseline.

---

## 12:00pm–4:00pm CDT — Background work runs

Every 2 hours Joshua pulls new telemetry and updates GHL tags. Darrell and Brenda do the same for their campaigns. You don't need to touch anything during this window.

What should be happening in the background:
- Ryan agent runs at 8:34, 10:34, 12:34, 2:34, 4:34 CDT → new dealerships flow to HubSpot + (if enrolled) Instantly
- Tyler agent runs at 8:30, 10:30, 12:30, 2:30, 4:30 CDT → new prospects flow to GHL + Instantly
- Marcus agent runs same schedule → new prospects flow to Attio

---

## End of day — Tuesday pre-work

Before leaving Monday, check:

1. **Final sends count** on Tyler campaign — should be 18 or very close
2. **New leads added today** in all 3 campaigns — the agents should have added 3-10 new prospects per business
3. **BOX BOX alerts** in your inbox — Joshua sends internal notifications if any lead hits P1/P2 (replied or clicked)

If everything looks clean Monday night, Tuesday is hands-off until 9am for Step 2 sends.

---

## Contact escalation path

If things go sideways and you can't figure it out:

1. Start a new Claude Code session in this repo
2. Say: "Read memory/project_session_handoff_2026_04_19.md and docs/MONDAY_LAUNCH_RUNBOOK.md. Tyler's Instantly campaign isn't sending. Diagnose."
3. Claude will pick up the full context and continue from where this session ended.

---

## The forward-looking checklist (optional if time permits)

If Monday fires cleanly and you have bandwidth:

- [ ] Build 3 GHL SMS workflows from `docs/TYLER_GHL_SMS_WORKFLOWS.md` (~30 min)
- [ ] Rebuild Marcus Real Estate + Home Builder sequences from `docs/MARCUS_4_SEQUENCES.md` REBUILD PLAYBOOK section (~1 hour, requires Attio UI)
- [ ] Archive the draft "Workflow A - Tyler Prospect AI Outreach" in GHL (legacy, not firing)

None of these are blockers. All can wait until Tuesday or later.
