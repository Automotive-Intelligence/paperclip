# dealer-audits — AvI Dealer Digital Diagnostics site

Static Vercel site publishing Automotive Intelligence's dealership audit
teardowns, with lead capture wired to paperclip's `/lead/ingest` (the funnel
system of record).

## Layout

```
dealer-audits/
├── index.html                  # hub: hero, latest audits, engagement ladder, lead form
├── audits/<slug>.html          # GENERATED report pages — do not hand-edit
├── specs/<slug>.json           # the source of truth for each audit
├── _template/report.html.tpl   # shared report page template (design + form)
├── generate.py                 # specs → audits renderer
├── api/lead.js                 # Vercel serverless fn → paperclip /lead/ingest
├── assets/                     # AvI logo + Archivo/InterTight woff2 subsets
└── vercel.json                 # cleanUrls + cache/security headers
```

## Publishing a new audit (Chase's workflow)

1. Research the dealership (seo-audit skill + SERP/citation/review intel).
2. Copy `specs/sutherlin-kia-huntington-beach.json` to `specs/<new-slug>.json`
   and rewrite every field. Strings are **trusted author HTML** — inline
   `<b>`, `<span class="mono">`, links etc. are fine; never render a spec
   from an untrusted source. Escape `&` as `&amp;`.
   - `severity`: `crit` | `warn` | `low` (finding stripe color)
   - `chip_class`: `p0` | `p1` | `p2` — pair with a matching `chip` label
   - `tone` on scorecard cards: `crit` | `warn` | `good` | `neutral`
   - `gauge_pct`: how much of the hero gauge ring is filled (D– ≈ 76)
   - `form_source`: unique per audit, e.g. `audit-<slug>` — this is how we
     attribute the lead in paperclip
   - `noindex`: keep `true` while the audit is a private sales asset;
     flip to `false` to let it rank
3. `python3 generate.py specs/<new-slug>.json`
4. Add a report card for it on `index.html` (copy the existing `.report` block).
5. Commit spec + generated page + hub edit, push. Vercel auto-deploys.

To restyle every audit at once: edit `_template/report.html.tpl`, run
`python3 generate.py` (no args = regenerate all specs), commit.

## Lead flow

Form (hub `#contact` + every report `#book`) → `POST /api/lead` (serverless)
→ paperclip `POST /lead/ingest` with `{brand: "avi", source: <form_source>}`.
Ingest is fail-closed: lead stored durably first, human always alerted; if it
reports failure the visitor is shown the direct-email fallback instead of a
fake success. Honeypot field `company_website` silently drops bots.

Env override: set `LEAD_INGEST_URL` in Vercel project settings to point at a
different paperclip deployment (defaults to production Railway).

## Deploy

Vercel dashboard: import the repo, **Root Directory = `dealer-audits`**,
Framework = Other, no build command. Or CLI: `vercel --prod` from this
directory. `api/lead.js` is picked up automatically as a serverless function.
