# Ryan Data — Instantly Campaign: Automotive Intelligence

**For: Automotive Intelligence · Sender: michael@automotiveintelligence.io**
**Recipients: Ryan Data's trigger-event dealership prospects nationwide**
**Platform: Instantly.ai**
**Updated: 2026-04-12**

---

## Campaign structure

| Step | Delay | Purpose |
|---|---|---|
| 1 | 0 (immediate) | Observation + operational gap insight |
| 2 | 3 days | Pattern insight + speed-to-lead data |
| 3 | 3 days | Social proof / case study with real numbers |
| 4 | 4 days | Breakup — leaves door open |

**Threading:** Steps 2-4 have blank subjects so Instantly sends them as replies in the same thread as Step 1.

---

## Step 1 — Day 0

**Subject:**
```
{{first_name}}, something I noticed about {{company_name}}
```

**Body:**
```
{{first_name}},

I came across {{company_name}} while researching dealerships in {{city}}.

{{verified_fact}}

Here's what I keep seeing across dealerships right now: the ones winning aren't spending more on leads. They're responding faster, following up more consistently, and using AI to handle the operational gaps that bleed margin — BDC response time, service appointment no-shows, and lead follow-up that falls through the cracks.

The gap between a dealership that's "doing fine" and one that's dominating their PMA usually isn't inventory or pricing. It's operational speed.

We built a free AI Readiness Assessment that shows you exactly where those gaps are at {{company_name}} — no cost, no obligation, just a clear picture.

Worth 15 minutes this week?

— Michael Rodriguez
Automotive Intelligence · michael@automotiveintelligence.io
```

---

## Step 2 — Wait 3 days

**Subject:** (blank — threads as reply)

**Body:**
```
{{first_name}},

Quick follow-up.

Here's the number that should bother every dealer: the average BDC response time on an internet lead is 4 hours. The dealers closing at 20%+ are responding in under 4 minutes.

That's not a people problem. Your BDC team is doing their best with the tools they have. It's a systems problem — and the dealers investing in AI right now aren't doing it because they're cutting-edge. They're doing it because they've done the math.

One extra deal per month from faster follow-up pays for a year of AI implementation. And that's just the internet lead side — service department no-shows, CSI score improvement, and fixed ops scheduling are all part of the same equation.

Curious whether that resonates with what you're seeing at {{company_name}}.

— Michael Rodriguez
Automotive Intelligence
```

---

## Step 3 — Wait 3 days

**Subject:** (blank — threads as reply)

**Body:**
```
{{first_name}},

Quick story — a dealership was spending $40K/month on third-party leads and closing at 8%. Their BDC was responding to internet leads in 3-6 hours. Follow-up sequences were inconsistent. Service appointments had a 22% no-show rate.

We ran an AI Readiness Assessment. Three things jumped out:
1. Speed-to-lead was costing them 4-5 deals per month
2. 40% of service appointments had no confirmation or reminder
3. Their follow-up sequence stopped after 2 touches (industry best practice is 7-9)

They didn't spend more on leads. They fixed the systems behind the leads. Within 90 days, close rate went from 8% to 14% on the same lead volume. Service no-shows dropped to 9%.

If {{company_name}} is spending on leads and not sure what's converting — or losing deals between the lead and the appointment — that's exactly what the assessment uncovers.

— Michael Rodriguez
Automotive Intelligence
Book a time: https://calendly.com/autointelligence/assessment
```

---

## Step 4 — Wait 4 days (Breakup)

**Subject:** (blank — threads as reply)

**Body:**
```
{{first_name}},

Last email from me. I know you've got a dealership to run and my emails are somewhere between the DMS report and the OEM compliance deadline.

If there's ever a point where {{company_name}} wants to understand where AI fits in your operation — BDC, service, follow-up, the full stack — I'm here. The assessment is free and takes 15 minutes. No pitch, no pressure.

Appreciate your time.

If the timing is ever right: https://calendly.com/autointelligence/assessment

— Michael Rodriguez
Automotive Intelligence · michael@automotiveintelligence.io
```

---

## Instantly merge tags reference

| Tag | Source | Example |
|---|---|---|
| `{{first_name}}` | Lead first_name | "John" |
| `{{last_name}}` | Lead last_name | "Smith" |
| `{{company_name}}` | Lead company_name | "Park Place Lexus" |
| `{{city}}` | custom_variables.city | "Plano" |
| `{{business_type}}` | custom_variables.business_type | "Lexus Dealership" |
| `{{verified_fact}}` | custom_variables.verified_fact | "Named DealerRater Dealer of the Year 2025" |
| `{{trigger_event}}` | custom_variables.trigger_event | "New GM appointed in February 2026" |
| `{{competitive_insight}}` | custom_variables.competitive_insight | "Sewell BMW responds to internet leads in 8 minutes vs 4 hours" |
| `{{group_affiliation}}` | custom_variables.group_affiliation | "Park Place Dealerships" |

## What NOT to do

- Do NOT mention pricing ($2,500 audit or $7,500 implementation) in any email
- Do NOT position as a vendor — position as a consultant who understands dealership operations
- Do NOT lead with "AI" in subject lines — dealers are skeptical of AI hype
- The assessment is FREE — that's the only offer in the email sequence
- Do NOT mention specific OEM programs or incentives (could be outdated)
- Do NOT reference DFW or any specific metro — campaign is nationwide

## Instantly campaign setup

1. Create campaign in Instantly: "Ryan Data — US Dealerships"
2. Add 4 steps with the copy above (Steps 2-4 with blank subjects for threading)
3. Set delays: 0, 3, 3, 4 days
4. Connected sending account: michael@automotiveintelligence.io
5. Launch campaign
6. Copy campaign ID → add to Railway as `INSTANTLY_CAMPAIGN_RYAN_DATA`

## Process going forward

1. Ryan Data prospects daily at 8:34 AM CST
2. Finds 3 trigger-event dealerships with deep research
3. Creates contact in HubSpot (CRM data + deal)
4. Adds lead to Instantly campaign (with all custom variables)
5. Instantly sends 4 emails over ~13 days
6. Reply comes in → Michael closes with free assessment → $2,500 audit → $7,500 implementation
