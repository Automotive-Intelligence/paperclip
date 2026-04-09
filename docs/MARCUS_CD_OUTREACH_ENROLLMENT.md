# Marcus CD Outreach Sequence — Enrollment Guide

**State as of 2026-04-09 14:30 CST**

## Where things stand right now

| | Count | Status |
|---|---|---|
| People in Attio | 213 | All tagged with `pipeline_stage` |
| `ownerphones-warm` (ready to enroll) | **197** | Linked Companies, all `cd_*` fields populated, merge fields verified |
| `marcus-salvage` (manual review later) | 16 | Real Dallas SMBs from old Marcus scrapes |
| Marcus CD Outreach Sequence | Drafted | Step 1 written, sending window 9-5 CST business days, NOT YET PUBLISHED |
| Workflows | 0 | None built yet |

## What you're about to do (10 minutes total)

### Step 1 — Enroll the 197 in the sequence (5 min)

1. Open Attio → **Sequences** → **Marcus CD Outreach Sequence**
2. Top right, click **Enroll recipients**
3. In the picker, click the **Filter** button
4. Add filter: **`Pipeline Stage`** **`is`** **`ownerphones-warm`**
5. Select all 197 (there should be a "select all" checkbox at the top of the list)
6. Click **Add to sequence** (or whatever the confirm button says)

You'll be back on the sequence page. Recipients count should now show **197**.

### Step 2 — Publish + enable (1 min)

1. Top of the page, click **Publish sequence** (the blue button in the info banner)
2. Top right, toggle **Enable sequence** to ON

That's it. The first emails will fire **tomorrow at 9 AM CST** through your connected Gmail account, sending from `michael@calling.digital` (or whatever your sequence sender is configured as in Settings).

### Step 3 — Sanity check (2 min)

After enabling, click the **Recipients** tab on the sequence. You should see:
- 197 recipients listed
- Each one showing "Scheduled: tomorrow 9-5 CST"
- All with status "Pending first email"

Click into 1-2 random recipients and confirm the merge fields will resolve (the preview will show the actual `Plastic and Reconstructive Surgery` company name instead of `{{company.name}}` placeholder).

### Step 4 — Walk away (2 min)

Close the laptop. The sequence runs on its own. Your inbox (`michael@calling.digital`) will start showing replies as they come in over the next 1-3 days.

## What to expect

**Day 1 (tomorrow):** All 197 emails go out between 9 AM and 5 PM CST in random batches (Attio paces them so you don't look like spam to Gmail)

**Day 2-4:** Replies start landing in your Gmail. Each reply auto-removes that contact from the sequence — Attio handles that natively, you don't have to do anything.

**Day 5+:** If we want a follow-up sequence, we add Step 2 to the same sequence in the editor (next to "Add step to sequence" button at the bottom). I can draft the copy when you're ready.

**Reasonable expectations from a 197-contact cold cold-email batch with personalized merge fields:**
- Open rate: 30-50% (Gmail-from-Gmail tends to inbox well)
- Reply rate: 1-3% (= 2-6 replies)
- Of those replies, 10-30% might be qualified conversations (= 0-2 real prospects you'd want to call)

This is a starting point, not a finish line. The data we'll learn from this batch (what subjects open, what verticals reply, what time of day works best) feeds into the next round.

## What NOT to do

- ❌ **Don't add the 16 marcus-salvage records** to this sequence. They have different reason codes and the merge fields might be inconsistent. Save those for a manual personal touch later.
- ❌ **Don't toggle Enable sequence ON before clicking Publish.** The sequence has to be published first.
- ❌ **Don't send to all 213 at once** — the salvage batch should be reviewed manually before any automated outreach.
- ❌ **Don't reply on behalf of the AI.** When real replies come in, YOU read them and decide whether to book a call. The whole point is Marcus warms them up so you close them.

## Troubleshooting

**"Enroll recipients" button is grayed out** → Sequence isn't published yet. Click Publish sequence first.

**Filter doesn't show "Pipeline Stage" as an option** → Refresh the page. The attribute was created via API at ~2:25 PM CST and Attio's UI sometimes caches the attribute list.

**Merge field shows `{{company.name}}` literally instead of the company name** → That contact has no linked company. Skip them or fix the link in the People record. Should not happen with the OwnerPhones cohort — all 197 are linked.

**Email goes to spam** → That's a deliverability issue with your Gmail sender, not Attio. Warm up the sending account by replying to a few cold emails yourself manually first, then re-test.

## After this batch — what's next

Once you've enabled the sequence and seen first emails go out, the highest-leverage next moves are:

1. **Marcus CD Outreach Sequence Step 2-4** — write follow-up emails that fire 3, 5, and 7 days after Step 1 if no reply. I can draft these when you're ready.
2. **Build a Workflow in Attio UI** — auto-enroll any new Person with `pipeline_stage = ownerphones-warm` into the sequence. Means future CSV imports flow into the sequence with zero clicks.
3. **Resume Marcus prospecting (or don't)** — once you see how the OwnerPhones cohort performs, decide whether you want Marcus generating fresh prospects on top of OwnerPhones or whether OwnerPhones is enough volume for now.

## Reference

- Attio workspace: Calling Digital
- Sequence URL pattern: `https://app.attio.com/calling-digital/sequences/<id>`
- Sender: connected Gmail account (likely `michael@calling.digital`)
- Pipeline stage attribute: `pipeline_stage` (single-select on People)
- Vertical attribute: `vertical` (single-select on People)
- Company custom fields used by sequence: `cd_industry`, `cd_prospect_notes`
- Live dashboard: https://paperclip-production-ba14.up.railway.app/dashboard
