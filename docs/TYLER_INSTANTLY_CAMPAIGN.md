# Tyler — Instantly Campaign: The AI Phone Guy

**For: The AI Phone Guy · Sender: info@theaiphoneguy.ai (Google Workspace)**
**Recipients: Tyler's trigger-event prospects in DFW 380 Corridor**
**Platform: Instantly.ai**
**Updated: 2026-04-12**

> **Why Instantly instead of GHL:** GHL email service for `mg.theaiphoneguy.ai` hit a 24% bounce rate and 61% delivery failure because Tyler's pre-cleanup prospects contained 178 hallucinated domains with no valid MX records. Sender reputation is damaged. Instantly provides proper sender warmup and Tyler now sends from the warmed-up Google Workspace mailbox instead.

---

## Campaign structure

| Step | Delay | Purpose |
|---|---|---|
| 1 | 0 (immediate) | Observation + pain point quantification |
| 2 | 3 days | Pattern insight + competitive reframe |
| 3 | 2 days | Social proof / case study |
| 4 | 3 days | Breakup — leaves door open |

**Sending account:** `info@theaiphoneguy.ai`
**Sender name:** `Michael Rodriguez`

---

## Step 1 — Day 0

**Subject:**
```
{{firstName}}, quick question about {{companyName}}
```

**Preview text:**
```
Noticed something about how you handle calls in {{city}}...
```

**Body:**
```
Hey {{firstName}},

I came across {{companyName}} while researching {{business_type}} businesses in {{city}}.

{{verified_fact}}

{{trigger_event}}

Here's what I keep seeing with {{business_type}} businesses in the 380 Corridor: the work is great, but calls are slipping through the cracks. After-hours calls go to voicemail. Peak season overwhelms the front desk. And every missed call is a customer who calls the next company on Google.

The math is brutal. One missed HVAC call in July is a $500+ job that walked. Multiply that by 5 calls a week and you're leaving $10K/month on the table.

I built something that fixes this without adding staff.

Worth a 15-minute look? <a href="https://bit.ly/4sMZpTi" target="_blank">Book demo now</a>

Michael Rodriguez
The AI Phone Guy
```

---

## Step 2 — Wait 3 days

**Subject:**
```
one thing about {{companyName}}
```

**Preview text:**
```
The pattern I keep seeing with {{companyName}}'s competitors...
```

**Body:**
```
Hey {{firstName}},

Following up on my note earlier this week.

I looked at {{companyName}}'s Google reviews and your customers clearly love the work. But here's something worth knowing: {{competitive_insight}}

The pattern I see across {{business_type}} businesses in your area: the ones growing fastest aren't doing better work than you. They're just answering every call.

Their secret isn't more techs or a bigger team. It's that when someone calls at 7pm or during a Saturday emergency, a real voice answers, not voicemail. That one difference changes everything downstream. More booked jobs. Better reviews. Higher close rate on estimates.

Curious if that's something {{companyName}} is thinking about.

Worth a 15-minute look? <a href="https://bit.ly/4sMZpTi" target="_blank">Book demo now</a>

Michael Rodriguez
The AI Phone Guy
```

---

## Step 3 — Wait 2 days

**Subject:**
```
30% fewer missed calls
```

**Preview text:**
```
How one North Texas shop stopped losing after-hours calls.
```

**Body:**
```
{{firstName}},

Quick story. A {{business_type}} business in North Texas was missing about 30% of their inbound calls. Not because they didn't care. Because they were on jobsites, with customers, or closed for the day.

We set up Sophie, an AI receptionist that answers every call, books appointments, and captures the caller's info. Same phone number, zero disruption.

Within 30 days, they went from 30% missed calls to zero. Booked jobs went up. Google reviews mentioned "always answers the phone." And the owner stopped waking up to voicemails from yesterday's lost customers.

If {{companyName}} is missing calls, even a few a week, that's exactly what this solves.

Worth a 15-minute look? <a href="https://bit.ly/4sMZpTi" target="_blank">Book demo now</a>

Michael Rodriguez
The AI Phone Guy
```

---

## Step 4 — Wait 3 days (Breakup)

**Subject:**
```
closing the loop
```

**Preview text:**
```
Last note, door stays open for {{companyName}}.
```

**Body:**
```
Hey {{firstName}},

Last note from me. I know you're busy running {{companyName}} and my emails are somewhere between the supply house invoice and the next service call.

If missed calls, after-hours coverage, or front desk overwhelm is ever something you want to solve without hiring, the door is open. No pitch, no pressure.

Worth a 15-minute look? <a href="https://bit.ly/4sMZpTi" target="_blank">Book demo now</a>

Appreciate your time either way.

Michael Rodriguez
The AI Phone Guy · info@theaiphoneguy.ai
```

---

## Instantly merge tag reference

| Tag | Source | Example |
|---|---|---|
| `{{firstName}}` | Lead first_name | "Ken" |
| `{{lastName}}` | Lead last_name | "Brown" |
| `{{companyName}}` | Lead company_name | "Brown and Sons Plumbing" |
| `{{city}}` | custom_variables.city | "Denton" |
| `{{business_type}}` | custom_variables.business_type | "Plumbing" |
| `{{verified_fact}}` | custom_variables.verified_fact | "Family-owned plumbing company serving Denton since 1998" |
| `{{trigger_event}}` | custom_variables.trigger_event | "Recent Google review mentioning slow response to after-hours calls" |
| `{{competitive_insight}}` | custom_variables.competitive_insight | "Local competitor has 247 Google reviews with 4.8 rating" |

All custom variables are written by Tyler's push code in [tools/ghl.py](../tools/ghl.py) and passed to Instantly via [tools/instantly.py](../tools/instantly.py).

---

## Instantly campaign setup checklist

- [ ] Create campaign in Instantly: "Tyler — AI Phone Guy"
- [ ] Add 4 steps with the copy above
- [ ] Set delays: Step 1 = 0 days, Step 2 = 3 days, Step 3 = 2 days, Step 4 = 3 days
- [ ] Sending account: `info@theaiphoneguy.ai` (Google Workspace)
- [ ] Verify warmup is enabled on the sending account
- [ ] Launch campaign
- [ ] Copy campaign ID → add to Railway as `INSTANTLY_CAMPAIGN_TYLER`

---

## What NOT to do

- Do NOT mention pricing ($187 or $482) in any email
- Do NOT use em dashes in the email body
- Do NOT mention "AI" in subject lines (too spammy for service business owners)
- Do NOT send SMS to cold prospects (email only, CAN-SPAM compliant)
- Gift (demo) is the only CTA, not a free audit or assessment

---

## Process going forward

1. Tyler prospects at 8:30 AM CST (every 2 hours, weekdays)
2. Finds 3 trigger-event businesses in DFW 380 Corridor (plumbers, HVAC, roofers, dental, PI law)
3. Creates contact in GHL with CRM data + pipeline opportunity (deal tracking)
4. Adds lead to Instantly Tyler campaign with all custom variables populated
5. Instantly sends 4 emails over ~8 business days from `info@theaiphoneguy.ai`
6. Reply comes in → Michael books demo → closes on call
