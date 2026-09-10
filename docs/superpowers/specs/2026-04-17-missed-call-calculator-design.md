# Missed Call Calculator — Design Spec

**Date**: 2026-04-17
**Business**: The AI Phone Guy
**Owner**: Zoe (content) + Tyler (distribution)
**Status**: Approved — ready for implementation

---

## Overview

An interactive web calculator that shows DFW service business owners exactly how much revenue they're losing to missed calls. Prospects answer 4 questions, see a preview of their lost revenue, then enter their email for a full dashboard-style report with industry benchmarks and a demo CTA.

**Goal**: Capture warm leads before they're ready for a demo. Feed them into a 5-email nurture sequence via Instantly, while creating a CRM record in GHL for Tyler to track.

---

## Architecture

```
┌─────────────────────────┐     ┌──────────────────────────┐
│   VERCEL (Frontend)     │     │   PAPERCLIP API (Railway) │
│                         │     │                          │
│   Next.js app           │────▶│   POST /api/calculator/  │
│   - Hero landing page   │     │        submit            │
│   - 4-question calc     │     │                          │
│   - Preview (ungated)   │     │   Actions:               │
│   - Email gate          │     │   1. Validate input      │
│   - Full report dashboard│    │   2. Run calculations    │
│   - Demo booking CTA    │     │   3. Create GHL contact  │
│                         │     │   4. Create Instantly lead│
│   Calculator math runs  │     │   5. Return report data  │
│   client-side for       │     │                          │
│   instant preview       │     └──────┬──────────┬────────┘
└─────────────────────────┘            │          │
                                       ▼          ▼
                                ┌──────────┐ ┌──────────┐
                                │   GHL    │ │ INSTANTLY │
                                │          │ │          │
                                │ Contact  │ │ Lead +   │
                                │ + tag    │ │ nurture  │
                                │ + pipe   │ │ campaign │
                                └──────────┘ └──────────┘
```

### Frontend: Vercel (Next.js)

- Standalone Next.js app deployed to Vercel
- Dark + dashboard visual style (dark background, green/blue/purple metric cards)
- Calculator math runs client-side for instant preview (no API call until email submit)
- Responsive — must work on mobile (business owners check on phones)
- Domain: `theaiphoneguy.ai/calculator` or `calculator.theaiphoneguy.ai`

### Backend: Paperclip API (Railway)

- New endpoint: `POST /api/calculator/submit`
- Handles GHL contact creation and Instantly lead creation
- Returns calculated report data (benchmarks, projections, metrics)
- Reuses existing CRM router infrastructure in Paperclip

---

## User Flow

### Screen 1 — Hero Landing Page

**Headline**: "How Much Revenue Are You Losing to Missed Calls?"
**Subhead**: "Most DFW service businesses lose $3,000–$12,000/month to voicemail. Find out your number in 60 seconds."
**CTA Button**: "Calculate My Lost Revenue →"

No form fields. No friction. One button.

### Screen 2 — Calculator (4 Questions)

Questions presented one at a time with smooth transitions:

**Q1**: What type of business do you run?
- HVAC
- Plumbing
- Dental
- Roofing
- Legal
- Other service business

**Q2**: How many calls does your business get per week?
- Slider: 10–200, default 50

**Q3**: What percentage of calls go to voicemail or get missed?
- Slider: 10%–60%, default 30%

**Q4**: What's your average job/case value?
- Pre-filled based on Q1 selection, editable
- HVAC: $850 | Plumbing: $650 | Dental: $1,200 | Roofing: $8,500 | Legal: $3,500 | Other: $500

All client-side. No API call. Instant calculation.

### Screen 3 — Preview + Email Gate

**Show partial result (ungated)**:
> "Based on your numbers, you're missing approximately **X** calls every month."
> "At your average job value of **$Y**, that's up to **$Z** in potential revenue walking out the door."

**Gate the full report**:
"Get Your Full Revenue Report →"

Form fields:
| Field | Required |
|-------|:--------:|
| First Name | Yes |
| Email | Yes |
| Phone | Yes |
| Business Name | Yes |

Business type, calls/week, miss rate, avg job value are carried from the calculator (hidden fields).

**On submit**: POST to Paperclip API → returns full report data.

### Screen 4 — Full Report Dashboard

Dark dashboard UI with:

1. **Revenue Recovery Number** (large, green) — "You could recover **$X/month**"
2. **Three metric cards**:
   - Calls Saved (green) — missed calls that would be answered
   - Jobs Booked (blue) — estimated conversions at 25% rate
   - ROI (purple) — return vs. $187/month AI Phone Guy cost
3. **Industry Benchmark Comparison** — "The average {{business_type}} business in DFW misses {{benchmark}}% of calls. You're at {{their_pct}}%."
4. **12-Month Projection** — cumulative lost revenue chart (bar or line)
5. **The Fix Section** — "What if you answered every call, 24/7?" with recovered revenue number
6. **CTA**: "Book a 15-Minute Demo" → https://bit.ly/4sMZpTi

---

## Calculation Formula

```
missed_calls_per_month = weekly_calls × (miss_rate / 100) × 4.3
conversion_rate = 0.25
monthly_lost_revenue = missed_calls_per_month × conversion_rate × avg_job_value
annual_lost_revenue = monthly_lost_revenue × 12
recoverable_revenue = monthly_lost_revenue × 0.80  # 80% recovery with AI
monthly_roi = recoverable_revenue / 187  # vs. Founder Offer price
```

## Industry Benchmarks

| Industry | Avg Miss Rate | Avg Job Value | Monthly Lost Revenue (at 50 calls/wk) |
|----------|:---:|:---:|:---:|
| HVAC | 28% | $850 | $12,857 |
| Plumbing | 32% | $650 | $8,944 |
| Dental | 22% | $1,200 | $11,352 |
| Roofing | 35% | $8,500 | $127,925 |
| Legal | 38% | $3,500 | $57,190 |

---

## API Endpoint

### `POST /api/calculator/submit`

**Request body**:
```json
{
  "first_name": "John",
  "email": "john@example.com",
  "phone": "972-555-1234",
  "business_name": "DFW Comfort HVAC",
  "business_type": "HVAC",
  "calls_per_week": 60,
  "miss_rate": 30,
  "avg_job_value": 850
}
```

**Actions**:

1. **Validate** — all fields required, email format check
2. **Calculate** — missed_calls_monthly, lost_revenue, recoverable_revenue, roi
3. **GHL** — Create contact:
   - `first_name`, `email`, `phone`, `company_name` (from business_name)
   - `business_type` (native field)
   - Custom fields: `miss_rate`, `lost_revenue_estimate`
   - Tag: `lead-magnet-calculator`
   - Pipeline: AI Phone Guy → Stage: New Prospect
4. **Instantly** — Create lead:
   - Built-in: `first_name`, `email`, `phone`, `company_name`
   - Campaign: nurture campaign ID
   - `skip_if_in_campaign: true`
   - Custom variables: `business_type`, `calls_per_week`, `miss_rate`, `avg_job_value`, `missed_calls_monthly`, `lost_revenue_estimate`, `recoverable_revenue`
5. **Return** — Full report data:
```json
{
  "missed_calls_monthly": 77,
  "monthly_lost_revenue": 16362,
  "annual_lost_revenue": 196350,
  "recoverable_revenue": 13090,
  "monthly_roi": "70x",
  "calls_saved_monthly": 62,
  "jobs_booked_monthly": 15,
  "benchmark": {
    "industry": "HVAC",
    "avg_miss_rate": 28,
    "their_miss_rate": 30,
    "percentile_rank": 65
  }
}
```

### Instantly Merge Tag Mapping

| Calculator Field | Instantly Field Type | Merge Tag |
|---|---|---|
| `first_name` | Built-in | `{{firstName}}` |
| `email` | Built-in | `{{email}}` |
| `phone` | Built-in | `{{phone}}` |
| `business_name` | Built-in (`company_name`) | `{{companyName}}` |
| `business_type` | Custom variable | `{{business_type}}` |
| `calls_per_week` | Custom variable | `{{calls_per_week}}` |
| `miss_rate` | Custom variable | `{{miss_rate}}` |
| `avg_job_value` | Custom variable | `{{avg_job_value}}` |
| `missed_calls_monthly` | Custom variable (calculated) | `{{missed_calls_monthly}}` |
| `lost_revenue_estimate` | Custom variable (calculated) | `{{lost_revenue_estimate}}` |
| `recoverable_revenue` | Custom variable (calculated) | `{{recoverable_revenue}}` |

### GHL Field Mapping

| Calculator Field | GHL Field | Merge Tag |
|---|---|---|
| `first_name` | `contact.first_name` | `{{contact.first_name}}` |
| `email` | `contact.email` | `{{contact.email}}` |
| `phone` | `contact.phone` | `{{contact.phone}}` |
| `business_name` | `contact.company_name` | `{{contact.company_name}}` |
| `business_type` | `contact.business_type` | `{{contact.business_type}}` |
| `miss_rate` | `contact.miss_rate` (custom) | `{{contact.miss_rate}}` |
| `lost_revenue_estimate` | `contact.lost_revenue_estimate` (custom) | `{{contact.lost_revenue_estimate}}` |

---

## Visual Design

**Style**: Dark + Dashboard
- Dark background (`#0f172a` → `#1e293b` gradient)
- Green for revenue/recovery numbers (`#22c55e`)
- Blue for operational metrics (`#3b82f6`)
- Purple for ROI/multiplier (`#a855f7`)
- Metric cards with colored borders and subtle background tints
- Clean sans-serif typography (Inter or system font)
- Results page feels like a real-time data dashboard, not a marketing page

**Psychology levers**:
- Loss aversion: frame around money LOST in preview
- Opportunity framing: frame around money RECOVERABLE in full report
- Specificity: personalized numbers feel real vs. generic claims
- Reciprocity: free tool creates goodwill before asking for demo
- Anchoring: industry benchmarks make their number feel urgent
- Social proof: "X businesses have used this calculator" counter (future)

---

## Distribution Plan

1. **Tyler** — uses calculator link as soft CTA in cold SMS/outreach instead of hard demo ask
2. **Zoe** — promotes via social content: "Are you losing $10K/month to voicemail? Find out →"
3. **Paid** — Google Ads targeting "missed calls {{service}} business DFW"
4. **SEO** — Blog post "How Much Do Missed Calls Cost Your Business?" linking to calculator
5. **RevOps** — Calculator leads get scored by RevOps agent alongside outreach leads

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Frontend | Next.js (React) on Vercel |
| Backend API | FastAPI endpoint in Paperclip (Railway) |
| CRM | GHL (contact + tag + pipeline) |
| Email Nurture | Instantly (lead + nurture campaign) |
| Styling | Tailwind CSS |
| Charts | Recharts or Chart.js (12-month projection) |

---

## Out of Scope (for v1)

- PDF report generation (email the report) — future enhancement
- A/B testing different headlines — P3 from marketing audit
- "X businesses have calculated" social proof counter — add after launch
- Google Analytics / GA4 event tracking — P1 from marketing audit, separate task
