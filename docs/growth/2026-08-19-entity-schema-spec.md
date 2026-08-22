# Organization + ContactPoint + sameAs Schema — B&T ship spec

**Date:** 2026-08-19 · **Owner:** Build & Tech (ships) · **Why:** every brand's Organization schema is
bare — **zero `sameAs` anywhere** (audit finding). That's the cheapest disambiguation lever we have, and
the citation data proved we need it (AI conflates "Worship Digital" with a UK CRO agency and "Automotive
Intelligence" with unrelated data firms). Non-visual, additive, low-risk — no Iris gate. Ship to each
site's shared layout/`<head>` so it's sitewide.

## Template (one JSON-LD block per brand, in the shared layout)
```json
{
  "@context": "https://schema.org",
  "@type": "Organization",
  "name": "<brand name>",
  "url": "<canonical url>",
  "logo": "<absolute logo url — B&T knows the asset path>",
  "description": "<disambiguating one-liner, below>",
  "areaServed": "<below>",
  "contactPoint": {
    "@type": "ContactPoint",
    "telephone": "<below>",
    "contactType": "customer service",
    "areaServed": "US"
  },
  "sameAs": [ /* B&T: fill with VERIFIED owned profile URLs only */ ]
}
```

## Per-brand values (verified where noted)
| Brand | name | url | telephone (verified live) | areaServed | disambiguating description |
|---|---|---|---|---|---|
| The AI Phone Guy | The AI Phone Guy | https://theaiphoneguy.com | +1-817-670-9689 | Dallas–Fort Worth, TX | "AI phone-answering service for DFW home-service businesses" |
| Worship Digital | Worship Digital | https://worshipdigital.co | +1-817-662-2473 | Dallas / 380 Corridor, TX | "Founder-run digital marketing agency for small businesses in Dallas and the 380 corridor" (NOT the UK CRO agency) |
| Automotive Intelligence | Automotive Intelligence | https://automotiveintelligence.io | +1-817-635-1987 | Dallas–Fort Worth, TX | "AI-readiness and orchestration consultancy for DFW car dealerships" (NOT an automotive data vendor) |
| Agent Empire | Agent Empire | https://buildagentempire.com | (no phone — use `contactPoint.email` info@buildagentempire.com or omit) | Worldwide | "Education and community for people building AI agents" |

Bookd (bookd.cx) already has Organization + SoftwareApplication + one `sameAs` — B&T: just extend its
`sameAs` array with the rest of the verified owned profiles; no new block needed. Coordinate with Ryan
(co-owned).

## sameAs — HARD rule
B&T fills `sameAs` **only with owned profile URLs you can verify** — pull from `~/avo-telemetry/social_registry.jsonl`
(the real handles), plus the live GBP URL once each profile is claimed, plus confirmed LinkedIn / YouTube /
Facebook / X. **Do not guess or invent a profile URL** — a wrong `sameAs` points the entity graph at
someone else's account and is worse than an empty array. If a handle can't be verified, leave it out.

## Ship + verify
1. B&T adds the block to each brand's shared layout.
2. Verify with Google Rich Results Test (renders JS) — Organization valid, no errors.
3. Polaris re-checks: `sameAs` present on each homepage (the earlier crawl grep) + re-runs the brand-name
   citation query per brand to watch for the disambiguation improving over time.
