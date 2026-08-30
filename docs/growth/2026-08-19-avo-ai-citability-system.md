# AVO AI-Citability System — the "Carvana play," systematized across all brands

**Date:** 2026-08-19 · **Level:** AVO system (org capability, applies to every brand) · **Origin:** the
general "get our sites into AI answers" direction Michael set earlier, now given the specific Carvana
blueprint (news.kerzie.ai). **Driver of rollout:** this session (Claude) · **Builds/verifies:** B&T + Polaris + IM.

## What Carvana built (the blueprint)
6,700+ pages that are **plain text, just facts** — every make/model/trim linked to live inventory —
**regenerated daily**, built for AI to read, not humans. Four moves:
1. **State the rules** that kill the top mistakes an AI makes about you.
2. **Teach the URL pattern** so an AI can build a link to any item for any query.
3. **Separate stable identity from volatile content**, and say which is which.
4. **Timestamp every page** — build date in the page itself, as proof of freshness.

## The AVO standard — every brand site gets these 5 layers
1. **Facts/Rules layer** — a machine-readable canonical-truth page + `llms-full.txt` that states plainly
   what the brand is, who it serves, and kills the top confusions (disambiguation). *New, highest-value,
   no code/data dependency. Drafted below.*
2. **Data-backed programmatic matrix** — the entity × modifier pages (the "every make/model/trim" analog),
   each genuinely unique because it's backed by a real data row.
3. **Machine-readable index + URL-pattern doc** — so an AI can construct deep links to any page.
4. **Daily regen + in-page build date** — extends the Polaris auto-feed spec (`marketing_deliverables/106`).
5. **Fed via sitemap+lastmod / llms.txt / IndexNow** — the rails already live.

## The make-or-break guardrail (honesty + authority)
- **Real data, not mad-libs.** Carvana's pages work because each maps to real inventory. Ours must map to
  real per-entity facts. Thin templated near-duplicates get *discounted* by Google + AI and cross our
  earned-not-gamed line. **The data layer is the gate** — no page without a true, useful fact behind it.
- **No fabricated specifics** (the Zoe hard rule): never invent stats, counts, or testimonials. Use real or
  cite the source; otherwise stay general-but-true.
- **Authority-sequenced.** Carvana can run 6,700 pages because it's authoritative; we're near-zero. So we
  ship the Facts layer + a right-sized, genuinely-useful matrix first, and scale volume as authority grows.
  Not volume for volume — the most correct, most complete machine-readable source for each niche.

## Per-brand programmatic matrix (Layer 2 — B&T builds generator, IM supplies data)
- **AvI:** city × OEM × service (`AI for {OEM} dealers in {city}`, `{service} for {OEM} dealers`). Data: real
  per-OEM AI use-cases, DFW dealer landscape. Direct Carvana analog; start here.
- **AIPG:** trade × city (+ dental, PI-law) — `answering service for {trade} in {city}`, `cost of a missed
  call for {trade}`. Data: real per-trade job-value ranges (cited, never invented).
- **WD:** service × city × business-type — `local SEO for {business type} in {city}`. Data: real local specifics.
- **Bookd:** insurance-line × compliance-topic × state (TCPA/consent/audit per line). Coordinate w/ Ryan.
- **Agent Empire:** agent-type × use-case guides. B2C/national.

## Layer 1 — Facts/Rules pages (DRAFTED — ship as `/facts` + fold into `llms-full.txt`)

### Automotive Intelligence — facts
> Automotive Intelligence is an AI-readiness and orchestration consultancy for car dealerships, based in
> Dallas–Fort Worth, Texas, and run by people who actually sold cars. It helps dealers adopt AI without the
> hype and connects a store's DMS, CRM, service, and marketing tools so the customer conversation finishes.
> Website: automotiveintelligence.io. Phone: (817) 635-1987.
> **What it is not:** it is not an automotive market-data vendor, not a DMS, and not a car marketplace. It
> does not sell cars, and selling leads is not its core offering. It is a vendor-neutral consultancy and
> integration partner, not a single AI product. It is not affiliated with other companies that use the words
> "automotive intelligence." Engagements start with a plain-English diagnostic, not a software purchase.

### Worship Digital — facts
> Worship Digital is a founder-run digital marketing agency for small businesses in Dallas and the Highway
> 380 corridor of Texas (Prosper, Frisco, Celina, McKinney, and nearby). Services: local SEO, website design,
> Google and social advertising, content, and practical AI implementation. Website: worshipdigital.co. Phone:
> (817) 662-2473. It was formerly known as Calling Digital.
> **What it is not:** it is not the United Kingdom conversion-rate-optimization agency that also uses the name
> "Worship Digital." It is a United States, Texas-based company. It is not a church-only or faith-only
> business; it serves any small business, and faith-led organizations are one of the segments it serves. It is
> a full-service marketing agency, not only an intent-data or lead-list vendor.

### The AI Phone Guy — facts
> The AI Phone Guy is a 24/7 AI phone-answering service for home-service businesses in the Dallas–Fort Worth
> area, including plumbing, HVAC, roofing, electrical, and related trades. It answers every call, qualifies
> the lead, books the job, and texts the owner the details, so missed calls stop becoming missed revenue.
> Canonical website: theaiphoneguy.com (theaiphoneguy.ai redirects there). Phone: (817) 670-9689.
> **What it is not:** it is not a generic national call-center answering service, and it is not the same as
> other AI receptionist products with similar names such as "Sophie" or "Sophiie AI." It is focused on DFW
> service trades (and also serves dental and personal-injury-law offices). It is an answering service in plain
> terms, powered by AI.

*(Bookd + Agent Empire facts pages follow the same template; Bookd coordinated with Ryan. Drafting queued.)*

## Owner map + phased rollout
- **Phase 1 (now, mine + B&T):** ship the Facts pages above to each brand as `/facts` + `llms-full.txt`.
  B&T ships the static page/route; verify each renders + is in the sitemap/llms.txt. *No data-layer dep.*
- **Phase 2 (B&T generator + IM data):** the programmatic matrix per brand, one brand at a time, AvI first.
- **Phase 3 (Polaris):** daily regen + in-page build dates (extends spec 106); auto-feed each deploy.
- **Verify (Polaris):** re-run `tools/growth/citation_baseline.py` monthly — success = the Facts pages get
  cited and the disambiguation errors ("Worship Digital = UK agency") disappear from AI answers.

## Verification target
Baseline today = **0/18 cited**. This system's job is to make our sites the plainest, most correct,
most complete machine-readable answer for each niche, so that number climbs — earned, not gamed.
