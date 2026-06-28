# Portfolio SEO/AEO Audit — Claude Code (VS Code) prompt

**Why this exists:** the Claude Code *web* environment runs behind an egress proxy set to
"Package managers only," so it can't crawl live brand sites (403 on outbound CONNECT). Running
Claude Code **locally in VS Code** has full network access — so the live-crawl limitation
disappears and the audit can be done for real across every brand.

## Setup (once)
1. Clone/open this `paperclip` repo in VS Code so the skills (`.agents/skills/seo-audit`,
   `.agents/skills/ai-seo`) and first-party brand context are available to Claude Code.
2. Confirm local Claude Code has network (it does by default — no egress proxy locally).
3. Optional but recommended: have a Google PageSpeed Insights API key and Google Search
   Console access handy for the brands you own.
4. Paste the prompt below into Claude Code.

---

## The prompt (copy everything in the block)

```
You are an expert SEO + AEO (Answer Engine Optimization) consultant running locally in VS Code
with FULL network access. Use it — crawl the live sites directly. Do not guess on-page facts.

CONTEXT
I own a portfolio of brands (DBAs under CD LLC). Before auditing:
- Read .agents/skills/seo-audit/SKILL.md and .agents/skills/ai-seo/SKILL.md and follow those frameworks.
- Read config/principles.py and services/personas/cto.md for FIRST-PARTY brand positioning. That is
  the source of truth for what each brand does and who it targets.
- Do NOT infer a brand's purpose from third-party search results. Same-named companies exist and will
  pollute results (e.g., a UK CRO agency is also called "Worship"/"Worship Digital" — that is NOT us).
  Always disambiguate the entity and discard look-alike companies.

SITES TO AUDIT (live)
- Worship Digital — https://worshipdigital.co — Dallas digital marketing + AI implementation consulting for SMBs (incl. faith-led)
- The AI Phone Guy — https://theaiphoneguy.ai — AI receptionist for DFW service trades (plumbing, HVAC, roofing, dental, PI law)
- Automotive Intelligence — https://automotiveintelligence.io — AI-readiness for car dealerships
- Agent Empire — https://buildagentempire.com — B2C education/community teaching people to build AI agents
- Bookd — https://bookd.cx — compliance-first CRM for life insurance agents
- Calling Digital — https://calling.digital — confirm live status: legacy brand, likely should 301-redirect to worshipdigital.co

DO NOT audit cold-outbound domains (bestworshipdigital.com, allworshipdigital.com) as brand sites.
Only verify they are noindex and NOT linked from any brand domain (their cold-mail reputation must
not bleed into the brand domains).

FOR EACH SITE — crawl and VERIFY (never assume):
TECHNICAL: robots.txt; sitemap.xml; canonical host (apex vs www, clean 301s, no dual-200); HTTPS/HSTS;
  RENDER MODE — fetch raw HTML (curl) AND rendered HTML (headless browser) and compare; flag if core
  copy/headings only appear after JS (breaks AEO crawlers that don't run JS); indexation (site: query);
  Core Web Vitals via PageSpeed Insights/Lighthouse (mobile); confirm crm./staging subdomains are noindex.
ON-PAGE: title tags, meta descriptions, single H1, heading outline, OG/Twitter cards, image alt,
  internal linking, and JSON-LD schema (Organization/LocalBusiness/Service/FAQPage/Article).
ENTITY/BRAND: is the brand term contested by same-named orgs? Is there Organization schema with sameAs
  to owned profiles (GBP, LinkedIn, X, Facebook)? Recommend disambiguating qualifiers (e.g., "Dallas").
LOCAL (Worship Digital, The AI Phone Guy, Automotive Intelligence): Google Business Profile presence &
  completeness, NAP consistency across site + profiles, local landing content, review velocity.
AEO: answer-first formatting; question-shaped H2/H3; FAQ sections + FAQPage schema; statistics &
  citations; definitions/glossary; comparison content; /llms.txt; and AI-crawler posture in robots.txt
  (GPTBot, PerplexityBot, ClaudeBot, Google-Extended — allow if citations are wanted). List the priority
  queries a buyer would ask each brand's answer engine, and (where possible) test them in ChatGPT/
  Perplexity/Google AI Overviews/Gemini and record who is currently cited.
KEYWORDS: map primary commercial + local intent per brand; flag thin or missing money pages.

HOW TO CRAWL (local): curl with a real browser User-Agent for robots/sitemap/key pages; for JS-rendered
sites use the headless browser (Playwright/Chromium) to capture rendered HTML; run PageSpeed Insights for
CWV. If a site blocks bots or a domain doesn't resolve, note it and continue.

OUTPUT
- One markdown report per brand at docs/seo/<brand-slug>-AUDIT-<YYYY-MM-DD>.md with this structure:
  Executive summary table → Critical findings (P0) → Technical → On-page → Entity/brand → Local (if
  applicable) → Keyword strategy → AEO → Prioritized action plan (P0/P1/P2, each with effort × impact).
  Tag every claim as [Verified — crawled] or [Inferred].
- Then write docs/seo/PORTFOLIO-SUMMARY-<YYYY-MM-DD>.md cross-cutting the SHARED issues. These sites were
  built the same way, so expect repeated problems (e.g., missing schema, JS-only rendering, no GBP, no
  canonical strategy). Call those out as "fix once, roll out to all" items, ranked by portfolio impact.
- Commit each report on a feature branch (do not push to main without asking).

START by confirming network access (curl one site and show the HTTP status), then proceed site by site.
Only ask me when a domain doesn't resolve, a site blocks crawling, or you need GBP/GSC access I haven't
provided.
```

---

## Notes
- There is already a partial, search-only audit of worshipdigital.co at
  `docs/SEO_AEO_AUDIT_worshipdigital_2026-06-28.md` (written in the web env where the live crawl was
  blocked). The local run should supersede it with verified on-page/technical findings.
- If you'd rather keep using the web environment for some sites, the alternative fix is to set that
  environment's network access to **"All domains"** (or add the specific domains to the allowlist) and
  start a fresh session — but local VS Code is simpler and unrestricted.
