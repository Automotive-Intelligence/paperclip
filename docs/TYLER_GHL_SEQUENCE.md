# Tyler — GHL Email Sequence: The AI Phone Guy

> **⚠️ SUPERSEDED 2026-04-12 20:30 CST**
>
> GHL email service for `mg.theaiphoneguy.ai` hit a 24% bounce rate and 61% delivery failure due to Tyler's pre-cleanup hallucinated domains. Sender reputation is damaged.
>
> **Use this file instead:** [TYLER_INSTANTLY_CAMPAIGN.md](TYLER_INSTANTLY_CAMPAIGN.md)
>
> Tyler now routes email delivery through Instantly from `info@theaiphoneguy.ai` (warmed-up Google Workspace mailbox). GHL remains as the CRM/pipeline tracking system only.

---

**For: The AI Phone Guy · Sender: Michael Rodriguez**
**Recipients: Tyler's trigger-event prospects in DFW 380 Corridor**
**Platform: GoHighLevel Workflow (RETIRED)**
**Updated: 2026-04-12**

---

## Sequence structure

| Step | Timing | Purpose |
|---|---|---|
| 1 | Immediate | Observation + pain point quantification |
| 2 | Wait 3 business days | Pattern insight + reframe |
| 3 | Wait 2 business days | Social proof / case study |
| 4 | Wait 3 business days | Breakup — leaves door open |

---

## Step 1 — Immediate

**Subject:**
```
{{contact.first_name}}, quick question about {{contact.company_name}}
```

**Preview text:**
```
Noticed something about how you handle calls in {{contact.city}}...
```

**Body:**
```
Hey {{contact.first_name}},

I came across {{contact.company_name}} while researching {{contact.business_type}} businesses in {{contact.city}}.

{{contact.verified_fact}}

{{contact.trigger_event}}

Here's what I keep seeing with {{contact.business_type}} businesses in the 380 Corridor: the work is great, but calls are slipping through the cracks. After-hours calls go to voicemail. Peak season overwhelms the front desk. And every missed call is a customer who calls the next company on Google.

The math is brutal — one missed HVAC call in July is a $500+ job that walked. Multiply that by 5 calls a week and you're leaving $10K/month on the table.

I built something that fixes this without adding staff.

Worth a 15-minute look? <a href="https://bit.ly/4sMZpTi" target="_blank">Book demo now</a>

— Michael Rodriguez
The AI Phone Guy
```

---

## Step 2 — Wait 3 business days

**Subject:**
```
one thing about {{contact.company_name}}
```

**Preview text:**
```
The pattern I keep seeing with {{contact.company_name}}'s competitors...
```

**Body:**
```
Hey {{contact.first_name}},

Following up on my note earlier this week.

I looked at {{contact.company_name}}'s Google reviews — and your customers clearly love the work. But here's something worth knowing: {{contact.competitive_insight}}

The pattern I see across {{contact.business_type}} businesses in your area: the ones growing fastest aren't doing better work than you. They're just answering every call.

Their secret isn't more techs or a bigger team. It's that when someone calls at 7pm or during a Saturday emergency, a real voice answers — not voicemail. That one difference changes everything downstream: more booked jobs, better reviews, higher close rate on estimates.

Curious if that's something {{contact.company_name}} is thinking about.

Worth a 15-minute look? <a href="https://bit.ly/4sMZpTi" target="_blank">Book demo now</a>

— Michael Rodriguez
The AI Phone Guy
```

---

## Step 3 — Wait 2 business days

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
{{contact.first_name}},

Quick story — a {{contact.business_type}} business in North Texas was missing about 30% of their inbound calls. Not because they didn't care — because they were on jobsites, with customers, or closed for the day.

We set up Sophie — an AI receptionist that answers every call, books appointments, and captures the caller's info. Same phone number, zero disruption.

Within 30 days, they went from 30% missed calls to zero. Booked jobs went up. Google reviews mentioned "always answers the phone." And the owner stopped waking up to voicemails from yesterday's lost customers.

If {{contact.company_name}} is missing calls — even a few a week — that's exactly what this solves.

Worth a 15-minute look? <a href="https://bit.ly/4sMZpTi" target="_blank">Book demo now</a>

— Michael Rodriguez
The AI Phone Guy
```

---

## Step 4 — Wait 3 business days (Breakup)

**Subject:**
```
closing the loop
```

**Preview text:**
```
Last note — door stays open for {{contact.company_name}}.
```

**Body:**
```
Hey {{contact.first_name}},

Last note from me. I know you're busy running {{contact.company_name}} and my emails are somewhere between the supply house invoice and the next service call.

If missed calls, after-hours coverage, or front desk overwhelm is ever something you want to solve without hiring — the door is open. No pitch, no pressure.

Worth a 15-minute look? <a href="https://bit.ly/4sMZpTi" target="_blank">Book demo now</a>

Appreciate your time either way.

— Michael Rodriguez
The AI Phone Guy · info@theaiphoneguy.ai
```

---

## GHL merge fields reference

| Field | Source | Type |
|---|---|---|
| `{{contact.first_name}}` | GHL Contact first name | standard |
| `{{contact.company_name}}` | GHL Contact company name | standard |
| `{{contact.city}}` | GHL Contact city | standard |
| `{{contact.business_type}}` | HVAC, Plumbing, Dental, Roofing, PI Law | custom |
| `{{contact.trigger_event}}` | Why NOW is the right time for outreach | custom |
| `{{contact.verified_fact}}` | Tavily-verified research fact | custom |
| `{{contact.competitive_insight}}` | What their closest competitor does better | custom |

All custom fields are written by Tyler's GHL push code in [tools/ghl.py](../tools/ghl.py).

## What NOT to do

- Do NOT mention pricing ($187 or $482) in any email
- Do NOT mention "AI" in subject lines — sounds spammy to service business owners
- Do NOT send SMS to cold prospects — email only, CAN-SPAM compliant
- Gift (demo) is the CTA — not a free audit or assessment
