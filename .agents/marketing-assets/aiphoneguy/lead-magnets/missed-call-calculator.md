# The Missed Call Calculator — Lead Magnet Spec

**Business**: The AI Phone Guy
**Owner**: Zoe (marketing) → Tyler (sales handoff)
**Skill**: `lead-magnets` + `free-tool-strategy`
**Status**: Ready to build

---

## Purpose

Give DFW service-business owners a 60-second interactive tool that shows them — in dollars — how much revenue voicemail is costing them every month. Converts anonymous traffic into a qualified email list and warms prospects for Tyler's cold email + demo book.

## Tool Logic

**Inputs (4 questions):**
1. What industry are you in? *(HVAC / Plumbing / Electrical / Roofing / Garage Door / Landscaping / Other)*
2. What's your average job value? *($ range slider, $150–$5,000)*
3. How many calls do you miss per week? *(0–5 / 6–15 / 16–30 / 30+)*
4. What % of callers do you estimate become customers when you answer? *(10 / 20 / 30 / 40 / 50)*

**Output (single screen):**
```
You're losing about $X,XXX every month
to calls that go to voicemail.

That's $XX,XXX per year.
```

**Formula:**
```
missed_calls_weekly × 4.33 × close_rate × avg_job_value = monthly_lost_revenue
```

**Industry benchmark overlay** (authority anchor):
> "Industry data shows 62% of callers never leave a voicemail. The average HVAC company loses $47K/year to missed calls."

## Email Capture Flow

After the result displays:
- Primary CTA: *"Get the full breakdown + 3 ways to stop losing these calls"*
- Fields: First name + email + phone (optional)
- Consent language: *"Send me the report. I agree to receive helpful content from The AI Phone Guy. Unsubscribe anytime."* (CAN-SPAM compliant)

**Delivery**: Auto-emailed PDF with their custom number + the 5-email nurture sequence begins.

## Positioning Copy (for landing page)

**H1**: How Much Is Voicemail Costing Your Business?
**Subhead**: See your monthly missed-call revenue loss in 60 seconds. No email needed to run it.
**Proof strip**: "Used by 200+ DFW service businesses"
**Primary CTA button**: *Run My Number*

## Loss-Aversion Anchors (marketing-psychology skill)

- "Every ring you miss is money left on the table."
- "Your competitors answer in 3 rings. What happens on ring 4?"
- "The average missed call in your industry = $[dynamic value]."

## Distribution Plan

| Channel | Asset |
|---------|-------|
| Google Ads | "Missed call calculator" exact match + local service keywords |
| Zoe's blog | Embed on every post that mentions missed calls or response time |
| Tyler's cold email | Touch 2 CTA: *"Want to run the number on your own business? 60-sec tool"* |
| Local Facebook groups | Organic post with before/after screenshot |
| Chamber of Commerce partners | Co-branded embed for DFW chambers |

## Success Metrics

- **Tool completion rate**: ≥ 55% of visitors who land on the page
- **Email capture rate**: ≥ 28% of completions
- **Nurture → demo booked**: ≥ 8% of captured emails book a demo within 14 days
- **Payback**: 1 closed deal at $297/mo covers full build cost

## Build Order

1. Static landing page with the calculator (Webflow or Framer — 1 day)
2. Zapier → ConvertKit/GHL → PDF delivery (half day)
3. 5-email nurture sequence (see `email-sequences/lead-magnet-nurture.md`)
4. UTM tagging for Google Ads campaign
5. Embed widget for partner sites
