# Thank You Pages — Design Spec

**Date:** 2026-04-18
**Owner:** Michael Rodriguez
**Status:** Approved, implementation in progress

## Problem

Three businesses (Automotive Intelligence, Calling Digital, AI Phone Guy) all book sales meetings via calendar links (Calendly or GHL calendar). After booking, prospects land on the default Calendly/GHL confirmation page — generic, brandless, and missing the chance to set expectations before the call.

Goal: replace the default confirmation with a branded "You're booked" page per business, modeled after the LeadHaste thank you page. Prospects should arrive at the sales call with clear expectations and confidence they're not wasting their time.

## Scope

One lean, shared template deployed as a single Vite + React + Tailwind app on Railway. Three subdomains route to different content based on hostname.

**Out of scope (for now):**
- Testimonial sections
- Stats bars
- Video embeds
- Blog content
- Analytics wiring (can layer on later)

## Subdomains

| Business | Subdomain | Calendar |
|---|---|---|
| Automotive Intelligence | `book.automotiveintelligence.io` | Calendly (free) |
| Calling Digital | `book.calling.digital` | Calendly (free) |
| AI Phone Guy | `book.aiphoneguy.ai` | GHL calendar (supports redirect) |

## Page Structure

Every page has the same structure:

1. **Logo / brand mark** — small, top-left
2. **"You're booked." heading** with green checkmark
3. **Subhead** — "Check your email for the calendar invite. Here's what happens next:"
4. **3 numbered "what to expect" cards** — content varies per business
5. **Small footer** — business name + contact email

No CTA, no form, no upsell. Just clarity and confidence.

## Content — "What to Expect" Steps

### Automotive Intelligence
1. **We audit your dealership** — Before the call, we pull public data on your store, ad-spend signals, and tech-stack footprint. Insights specific to your rooftop, not generic slides.
2. **We map your AI opportunity** — On the call, we walk through where AI actually moves the needle: showroom capture, service follow-up, lost-lead recovery. No fluff.
3. **You leave with a plan** — Fit or not, you walk away with an AI readiness scorecard and 3 plays you can run this quarter.

### Calling Digital
1. **We audit your competitors** — Before the call, we map what your top 3 competitors are running for outbound and paid — subject lines, offers, ad copy.
2. **We map the gap** — On the call, we show the specific plays they're running that you're not, and what it'll take to flip that.
3. **You leave with a battle plan** — Fit or not, you leave with a one-page competitive brief you can run with this week.

### AI Phone Guy
1. **We pull your missed-call data** — Before the call, we estimate your miss rate and lost revenue based on your category and volume.
2. **We demo the AI agent live** — On the call, you'll hear your AI agent handle a real scenario — quoting, booking, triaging. Actual voice, not slides.
3. **You leave with a number** — Exact monthly cost, setup timeline, projected recovery. Yes or no, no chasing.

## Architecture

### App
- **Location:** `thankyou/` (new directory at project root)
- **Stack:** Vite + React 18 + TypeScript + Tailwind CSS
- **Routing:** Hostname-based. App inspects `window.location.hostname` on load and renders the matching business config.
- **Content:** Lives in a single `businesses.ts` config — one object per business with `name`, `logo`, `steps`, `email`. Adding a business later = adding one entry.

### Hostname routing logic
```
book.automotiveintelligence.io  → Automotive Intelligence config
book.calling.digital            → Calling Digital config
book.aiphoneguy.ai              → AI Phone Guy config
<anything else>                 → Default to a simple "select business" index
                                  (dev/preview use — shows all 3 for testing)
```

### Deploy
- **Railway:** single service, built with `vite build`, served with the `serve` npm package on Railway's `$PORT`
- **Custom domains:** three custom domains attached to the same Railway service, SSL auto-provisioned by Railway
- **DNS:** Michael adds CNAME records at each registrar pointing `book.<domain>` to the Railway-provided target

### Calendar configuration (user-side, post-deploy)
- **Calendly (AI + CD):** free plan doesn't allow auto-redirect. Paste the thank you page URL into the confirmation email template as "Here's what happens next: [link]". Upgrade path exists if auto-redirect becomes worth $12/mo.
- **GHL (AI Phone Guy):** set redirect URL directly in the calendar settings — free.

## Design System (visual)

- **Font:** Inter (Tailwind default) or system sans-serif
- **Accent color per business:**
  - Automotive Intelligence: blue (matches existing brand)
  - Calling Digital: slate/charcoal (professional, matches calling.digital)
  - AI Phone Guy: brighter / phone-vibes accent (cyan or teal)
- **Layout:** centered, single column, ~640px max width, generous whitespace
- **Checkmark:** green circle with white check SVG
- **Step cards:** light gray background, rounded, numbered bubble on left

## Success Criteria

- [ ] Three Railway URLs live and return the correct branded page based on hostname
- [ ] Mobile-responsive (Tailwind default breakpoints)
- [ ] Prospects land on branded page within 2 seconds
- [ ] Michael has a written checklist of the 3 DNS updates + 3 calendar updates he needs to make

## Deferred / Future

- Analytics (GA4 or Plausible, per subdomain)
- A/B testing headlines
- Adding video intro or testimonials
- Tracking which prospect booked (UTM + CRM webhook enrichment)
