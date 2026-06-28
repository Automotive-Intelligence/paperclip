# SEO & AEO Analysis — worshipdigital.co

**Prepared:** 2026-06-28
**Subject:** https://worshipdigital.co/ (Worship Digital — primary brand marketing site, hosted on Vercel)
**Business (per internal source of truth):** Dallas-based digital-marketing **+** AI‑implementation consultancy for SMBs, including faith‑led brands. Formerly *Calling Digital*; DBA under CD LLC. Strategic pivot: full digital-marketing suite today → "the leading AI implementation consultancy in Dallas" (target: 50% of revenue from AI services within 12 months).
**Frameworks used:** repo skills `.agents/skills/seo-audit` and `.agents/skills/ai-seo`.

---

## 0. Scope & a candid caveat

This environment's network policy **blocks outbound crawling of worshipdigital.co** (egress proxy returns 403; WebFetch is disabled for all external hosts). So this report does **not** assert live on‑page facts — I have not seen the rendered title tags, meta descriptions, H1s, schema, or PageSpeed scores. Doing so would be guessing.

Instead, every claim is sourced one of two ways:
- **Verified (internal):** taken from your own repo (`config/principles.py`, `agents/callingdigital/*.py`, `services/wd_dmarc_monitor.py`, `services/personas/*`). High confidence.
- **To verify (☐):** an on‑page/technical item you (or a crawler like Screaming Frog / Ahrefs / Google Search Console / PageSpeed Insights) should confirm. These are flagged with a checkbox.

**Fastest way to upgrade this to a fully verified audit:** paste the homepage **View‑Source** HTML (and 2–3 key service pages), or drop a Screaming Frog / Search Console export into the repo. I'll fill in every ☐.

> ⚠️ One thing I corrected mid‑analysis: a public web search for "Worship Digital" surfaces a **different company** — *Worship* (worship.agency / worshipdigital.co.uk), an award‑winning **UK CRO agency** in Manchester. That is **not you**. The fact that a same‑named, more‑established firm outranks you for your own brand term is itself a finding (see §2).

---

## 1. Executive summary

| Area | Read | Priority |
|---|---|---|
| **Brand‑name collision** (UK "Worship" agency + literal "worship/church" meaning) | High risk — your brand term is contested and ambiguous | **P0** |
| **Entity / branded SERP control** (GBP, schema, sameAs) | Likely thin for a recently‑renamed brand | **P0** |
| **Local SEO (Dallas/DFW)** — core to an SMB‑local agency | Make‑or‑break for "AI consultant Dallas"‑type intent | **P0** |
| **On‑page targeting** for the dual offer (marketing + AI) | Needs distinct pages per service + intent | **P1** |
| **AEO / AI‑search readiness** | Strategic must‑win — you *sell* AI; being cited by AI is proof | **P1** |
| **Technical (Vercel render/crawl, sitemap, canonical)** | Verify rendering & canonicalization | **P1** |
| **Cold‑outbound domain hygiene** (best/all‑worshipdigital.com) | Keep isolated from brand domain | **P2** |
| **Authority / off‑page** | Net‑new brand; build from zero deliberately | **P2** |

The single biggest strategic point: **your name works against you in search.** "Worship Digital" collides with (a) an established UK CRO agency and (b) the literal church/worship‑tech category. Unqualified, the term is ambiguous to both Google and AI answer engines. Winning SEO/AEO here is less about generic "rank higher" tactics and more about **establishing Worship Digital as a distinct, Dallas‑local, AI‑+‑marketing entity** that engines can disambiguate and trust.

---

## 2. Critical finding — brand‑name collision & entity ambiguity (P0)

**The problem (verified by search):**
- **`worship.agency` / `worshipdigital.co.uk`** — a real, award‑shortlisted UK CRO/UX agency (Manchester, founded 2009, clients incl. Skipton, WeBuyAnyCar). It owns the "Worship" + "Worship Digital" + CRO associations across Clutch, Trustpilot, LinkedIn, The Drum.
- **"worship digital" as a phrase** also strongly means *online church / digital worship / worship software* — a large, unrelated semantic field.
- Your domain is the **`.co`** (`worshipdigital.co`); the UK firm holds **`.co.uk`** and **`.agency`**. Near‑identical strings, different owners — classic recipe for SERP and citation confusion.

**Why it matters for both SEO and AEO:**
- Google must decide which "Worship Digital" entity a query refers to. An older, link‑rich UK agency will win the bare brand term by default.
- AI answer engines (ChatGPT, Perplexity, AI Overviews, Gemini) resolve **entities**, not just keywords. With two same‑named agencies, an LLM asked about "Worship Digital" may **merge or mis‑attribute** facts — a direct threat to a company whose whole pitch is AI competence.

**Recommendations:**
1. **Always qualify the brand.** In titles, H1s, schema, GBP, and link anchors, pair the name with disambiguating tokens: *"Worship Digital — Dallas AI & Digital Marketing Consultancy."* Never ship a bare "Worship Digital" `<title>`.
2. **Build a hard `Organization` entity** (see §6/§7): legal name, Dallas address, `sameAs` to every owned profile (LinkedIn, GBP, X/`worshipcro`? confirm handle, Facebook, etc.), founding, logo. Give engines an unambiguous node.
3. **Own a "Worship Digital vs. [the other Worship]"‑proof footprint** — consistent NAP + local signals so the *Dallas* entity is unmistakable.
4. **Strategic question to weigh (not urgent):** the name carries permanent SEO drag (contested term + church‑tech ambiguity). If a rename is ever on the table, this is a real input. If not, lean *all‑in* on the "Dallas AI" qualifier as your moat. → *Decision for you; flag only.*

---

## 3. Technical SEO — verify on Vercel (P1)

The site is a Vercel deployment. Vercel itself is fast and reliable; the SEO risks are about **rendering and canonicalization**, not hosting.

- ☐ **Rendering mode.** Is the homepage server‑rendered/static (Next.js SSG/SSR) or a client‑rendered SPA? **This is the highest‑value technical check.** If primary copy only appears after JS runs, many AI crawlers (and some SEO crawlers) see an empty shell. Confirm core content + headings are in the **initial HTML** (View‑Source, not Inspector). For AEO this is non‑negotiable — several answer‑engine crawlers do **not** execute JavaScript.
- ☐ **Canonical host.** Pick one — `https://worshipdigital.co` (apex) vs `www` — 301 the other, and set a self‑referential `<link rel="canonical">` on every page. Confirm no duplicate apex/www both 200.
- ☐ **robots.txt** exists, doesn't block JS/CSS, and references the sitemap.
- ☐ **XML sitemap** at `/sitemap.xml`, listing only canonical, indexable URLs; submitted in **Google Search Console** (set up GSC + Bing Webmaster if not already — these are your ground‑truth instruments).
- ☐ **Indexation.** Run `site:worshipdigital.co` in Google; confirm the right pages are indexed and the `crm.` subdomain is **not** (it should be `noindex`/robots‑blocked — it's your Twenty CRM, not marketing content).
- ☐ **Core Web Vitals.** Run PageSpeed Insights for mobile; target LCP < 2.5s, INP < 200ms, CLS < 0.1. Vercel + Next/Image usually makes this easy — verify image sizing and font loading.
- ☐ **HTTPS/HSTS, 404 handling, redirect chains** — confirm clean.
- ☐ **Open Graph / Twitter cards** present (title, description, 1200×630 image) — drives social + link‑preview CTR and helps entity signals.

---

## 4. Local SEO — Dallas/DFW (P0)

You sell to **Dallas SMBs**. For local‑intent queries ("AI consultant Dallas," "digital marketing agency near me"), the **Google Business Profile + local pack** matters as much as the website.

- ☐ **Claim & fully complete Google Business Profile** — categories (e.g., *Marketing agency*, *Business management consultant*), Dallas service area, hours, services list (digital marketing **and** AI implementation), photos, and a keyword‑aware description. GBP is often the #1 local ranking lever and a frequent AI‑answer source for "near me."
- ☐ **NAP consistency** — identical Name / Address / Phone on the site footer, GBP, LinkedIn, and citations. Inconsistent NAP is a top local‑ranking killer and worsens the entity‑collision problem in §2.
- ☐ **Local citations** — Bing Places, Apple Business Connect, industry/Dallas directories.
- ☐ **Reviews** — kick off a steady review flow from current clients (Paper & Purpose, Panda, Warden). Reviews feed both the local pack and AI "best agency in Dallas" answers.
- ☐ **Location/landing content** — a substantive "AI consulting & digital marketing in Dallas" page with genuinely local context (not a thin doorway page).
- Consider a **faith‑led / ministry** local angle as a differentiated sub‑segment (per `principles.py`), e.g., "digital marketing for Dallas churches & faith‑based organizations" — lower competition, on‑brand, and avoids the UK‑CRO collision entirely.

---

## 5. Keyword & content strategy — two offers, two intents (P1)

Your offer splits cleanly; your site architecture and content should too.

**A) Digital‑marketing services (trust‑builder, today's revenue):**
- Money pages, one per service: website builds, social media management, SEO, paid ads, email marketing, content strategy, brand development. Each with its own targeted title/H1, e.g., *"Small Business SEO Services in Dallas."*
- Target commercial‑local intent: "Dallas digital marketing agency," "social media management for small business," "[service] for small business Dallas."

**B) AI implementation consulting (the pivot, the differentiator):**
- This is where search competition is *thin* and your category‑creation play lives. Build the cornerstone:
  - **"AI consulting / AI implementation for small business (Dallas)"** — primary service pillar.
  - **"AI readiness assessment / audit"** — high‑intent, conversion‑oriented (Nova's 70%‑conversion offer); make it a lead magnet page.
  - Educational cluster (Nova/Sofia's content engine): "best AI tools for [plumbers / dentists / law firms / churches]," "how should a small business start with AI," "AI automation for [industry]." These are **bottom‑of‑funnel for you and top‑of‑funnel for AEO** (see §6).
- **Bundle content** mirroring your sales motion: marketing → AI Phone Guy (call handling) → full AI consulting. A "how our services work together" page supports the 60% bundle‑attach KPI.

**Content E‑E‑A‑T:** publish under **real named experts** — Nova (AI Implementation Director), Dek (CEO), Sofia (Content). Author bios with credentials feed Google's E‑E‑A‑T *and* AEO author/entity signals. A consultancy selling AI must *look* like practitioners, not a faceless agency.

---

## 6. AEO / Answer‑Engine Optimization (P1 — strategic)

For a firm whose pitch is "we help you adopt AI," **getting cited by AI engines is simultaneously lead generation and live proof of competence.** This deserves first‑class effort, not an afterthought.

How AI search picks sources (per `ai-seo` skill): structure, extractability, statistics, citations, freshness, and entity authority — *not* just rank position. A well‑structured page can be cited even when it ranks page 2–3.

**Make pages extractable & citable:**
- ☐ **Answer‑first formatting.** Lead each page/section with a direct 40–60‑word answer to the question it targets, then expand. LLMs lift self‑contained, quotable passages.
- ☐ **Question‑shaped headings** (H2/H3 as the actual questions a Dallas SMB owner asks) + an **FAQ section** on key pages.
- ☐ **Include statistics, named tools, and concrete outcomes** (e.g., Nova's "40% average efficiency gain," real implementation examples). Stats/citations measurably increase AI citation rates.
- ☐ **Definitions/glossary** for AI terms ("What is an AI readiness audit?") — definition blocks are heavily reused by answer engines.
- ☐ **Comparison content** ("AI consultant vs. doing it in‑house," "tool A vs B for SMBs") — comparisons are disproportionately cited.

**Structured data / machine‑readability:**
- ☐ `Organization` + `LocalBusiness` schema (entity + Dallas + sameAs — directly addresses §2).
- ☐ `Service` schema for each offering; `FAQPage` schema on FAQ blocks; `Article` + `author` (`Person`) schema on the blog.
- ☐ Consider an **`/llms.txt`** and clean, semantic HTML so LLM crawlers can map the site.
- ☐ Confirm AI crawlers (GPTBot, PerplexityBot, ClaudeBot, Google‑Extended) are **allowed** in robots.txt — *don't* block them if you want citations. (Decide deliberately: visibility vs. content‑use stance.)

**Off‑domain (where AI actually finds you):** AI answers cite third‑party sources far more than owned domains. Get listed/reviewed on **Clutch, G2, local Dallas roundups, "best AI consultants" listicles, podcasts, LinkedIn**. This is the same work that also fixes the entity‑collision problem.

**Baseline & monitor:** test your priority queries today across ChatGPT, Perplexity, Google AI Overviews, and Gemini — e.g., *"AI implementation consultant for small business in Dallas," "how can a Dallas small business adopt AI," "digital marketing + AI agency Dallas."* Record who's cited (likely competitors / directories, not you). Re‑test monthly; that's your AEO scoreboard.

---

## 7. On‑page checklist (homepage + templates) — to verify (☐)

- ☐ **Title tag:** unique, ≤ ~60 chars, qualified — e.g., `Worship Digital | AI & Digital Marketing Consultancy in Dallas`.
- ☐ **Meta description:** ~150 chars, value‑prop + Dallas + CTA.
- ☐ **One `<h1>` per page**, containing the primary qualified term.
- ☐ **Logical H2/H3** outline (also serves AEO §6).
- ☐ **Descriptive `alt` text** on images; compressed, correctly sized (CWV).
- ☐ **Internal linking:** homepage → service pillars → supporting articles; descriptive anchors (not "click here").
- ☐ **Clear, repeated CTA** (book AI readiness assessment / strategy call) — conversion, which compounds SEO value.
- ☐ **Visible NAP + Dallas signal** in footer (entity + local).
- ☐ **Organization/LocalBusiness JSON‑LD** in the HTML (see §6).

---

## 8. Domain & sender‑reputation hygiene (P2)

From `services/wd_dmarc_monitor.py`, your domain estate is:
- `worshipdigital.co` — **primary brand + Loops transactional mail** (this site).
- `bestworshipdigital.com` — cold‑outbound warmup (Smartlead).
- `allworshipdigital.com` — cold‑outbound warmup (Instantly).
- `crm.worshipdigital.co` — Twenty CRM (self‑hosted).

SEO/deliverability notes:
- ✅ DMARC monitoring already in place — good.
- ☐ **Keep the cold‑outbound domains fully isolated from the brand's link graph.** Don't link them to/from worshipdigital.co, and keep them `noindex`. Spam/cold‑mail signals must not bleed into the brand domain's reputation.
- ☐ **`crm.` subdomain:** confirm `noindex` + robots‑blocked so the CRM never appears in search.
- ☐ Brand‑domain email (Loops) stays on `worshipdigital.co` with correct SPF/DKIM/DMARC — already monitored; just keep transactional and cold streams on separate domains (you do). This protects both inboxing and the trust signals that increasingly feed brand‑entity SEO.

---

## 9. Prioritized action plan

**P0 — Entity & local foundation (do first, highest ROI)**
1. Claim + fully optimize **Google Business Profile** (Dallas, dual‑service, photos, description). [§4]
2. Lock **NAP consistency** site‑wide + across profiles; add **Organization/LocalBusiness schema** with `sameAs`. [§2/§4/§6]
3. **Qualify the brand everywhere** — kill any bare "Worship Digital" title/H1; add "Dallas AI & Digital Marketing." [§2]
4. Stand up **Google Search Console + Bing Webmaster**; submit sitemap; baseline indexation. [§3]

**P1 — On‑page, content & AEO**
5. Verify **Vercel rendering** (content in initial HTML) + canonical host. [§3]
6. Build **distinct service pages** (each marketing service + AI consulting + AI readiness audit) with proper titles/H1/schema. [§5/§7]
7. Launch the **AI‑education content cluster** under named experts; answer‑first + FAQ + stats formatting. [§5/§6]
8. **Baseline AI‑answer visibility** across ChatGPT/Perplexity/AIO/Gemini; set monthly re‑test. [§6]

**P2 — Authority & hygiene**
9. Seed **reviews** (current clients) + **third‑party listings** (Clutch/G2/Dallas roundups) — fixes authority *and* entity collision *and* AEO citation sources. [§4/§6]
10. Confirm **cold‑domain isolation** + `crm.` noindex. [§8]
11. Build **backlinks** via local PR, partnerships, guest content, the faith‑led niche angle. [§2/§5]

---

## 10. What I need to make this a fully verified audit

Drop any of these into the repo (or paste inline) and I'll convert every ☐ into a confirmed finding with exact fixes:
1. **Homepage View‑Source HTML** + 2–3 key page sources (most valuable).
2. **Google Search Console** access/export (queries, coverage, CWV).
3. A **Screaming Frog / Ahrefs / Semrush** crawl export.
4. **PageSpeed Insights** results (mobile) for the homepage + one service page.

Alternatively, whitelist `worshipdigital.co` in this environment's egress policy and I'll crawl it directly.

---

*Prepared from first‑party brand context in this repo. On‑page/technical specifics are marked ☐ pending direct site access, which the current network policy blocks. Third‑party web‑search results about "Worship Digital" were reviewed and excluded where they referred to the unrelated UK agency `worship.agency`.*
