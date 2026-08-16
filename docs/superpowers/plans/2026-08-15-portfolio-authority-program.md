# Portfolio Authority Program — Implementation Plan

> **For agentic workers:** this is a growth/content program plan, not a code build. Tasks use checkbox (`- [ ]`) syntax. "Verification" on each task is a real-world metric (indexed / schema-valid / ranking-delta / citation-check via the searchstack gauge), not a unit test. Executors are AVO seats (CMO, Internal Marketing, Iris, Build & Tech, CRO, Polaris), not one engineer.

**Goal:** Move the portfolio from "indexed but unranked and uncited" to "ranking page 1 and cited by AI answer engines" on priority buyer queries — by building genuine, earned authority.

**Architecture:** Three pillars from the ai-seo framework — (1) best-answer content, (2) third-party authority, (3) real results / E-E-A-T — sequenced to capture existing equity first, then compound. Polaris measures the whole thing on a weekly scoreboard; CMO prioritizes; Internal Marketing writes; Iris gates visuals; Build & Tech ships; CRO/founder open third-party doors.

**Tech Stack:** Existing Next.js/Vercel brand sites; Polaris crawl-feed + GSC (WIF pipeline); the searchstack citation gauge (see the paired Polaris flag); no new platform.

**Spec:** This plan argues from the diagnosis Polaris surfaced and I re-verified live over 2026-06-28 → 2026-08-15: **we are SEEN but never CLICKED** — ~7,320 impressions, 9 clicks, ranking pages 5–6, near-zero authority. Findability is already won (sites technically sound; canonical/robots/llms.txt/IndexNow/lastmod live). The wall is **authority**, and no repo or tool clears it — only better answers, real reputation, and proof do. Fifth-Avenue standard per `config/principles.py` DIRECTION.

## Global Constraints
- **Earned, not gamed.** No auto-rewrite / keyword-stuffing / engine-gaming tactics. Authority is built by being the genuinely best, most trustworthy answer. This is the `principles.py` integrity line: *act as if in excellence, tell the whole truth in fact.* [[feedback_marketing_superpowers_and_conversion]]
- **Hero-metrics policy** — every number traceable; "unknown — needs check," never a fabricated stat. [[feedback_hero_metrics_policy]]
- **Iris visual gate** before any visual/web change merges. [[feedback_iris_visual_gate_before_merge]]
- **No em-dashes** in any outbound/brand-facing copy. [[feedback_no_em_dashes_general]]
- **Minimize spend** — gate paid steps; report cost unprompted. [[feedback_minimize_generation_spend]]
- **Respect scope; hand off, don't drift.** Each task names its owning seat; that seat ships, others spec/verify. [[feedback_respect_persona_scope]]
- **Verification is the deliverable.** No task is "done" on publish — done = the metric moved or the artifact is live-verified.

---

## Phase 0 — Baseline + target queries (owner: Polaris + CMO)

### Task 0.1: Citation + ranking baseline (Polaris)
**Deliverable:** a single baseline table, all brands, from the searchstack gauge (once wired — see paired flag) + the GSC WIF pipeline.
- [ ] Pull GSC TOTALS-mode clicks/impressions/avg-position per brand (already flowing).
- [ ] Run searchstack `ai`/`geo` citation check per brand on its top 5 buyer queries; record cited yes/no + who is cited instead.
- [ ] Write the baseline into `growth_analytics_state.md` as the Phase-0 anchor.
- **Verify:** baseline table exists with a real citation count (expected: near-zero) and per-query positions. This is the number every later phase moves.

### Task 0.2: Priority query map (CMO)
**Deliverable:** 5 priority commercial/local queries per brand, ranked by (buyer intent × existing position × volume).
- [ ] For each brand, list the 5 queries a real buyer types. Seed from `docs/seo/` keyword sections + GSC's already-ranking queries.
- [ ] Flag the "already ranking, just buried" queries (fastest wins) vs "not ranking at all" (slow builds).
- **Verify:** CMO-approved query map committed; Phase 1 targets the buried-but-ranking subset.

---

## Phase 1 — Capture existing equity (the fast win) (owner: Internal Marketing + Build & Tech, measured by Polaris)

Rationale: the cheapest authority is a page that already ranks page 5 for a real query. Strengthen it to page 1–2 before writing anything net-new.

### Task 1.1: Worship Digital local-SEO equity
**Deliverable:** the two queries Polaris found — `what is local seo` (910 impr, pos ~45) and `local seo marketing` (810 impr, pos ~46), ~1,720 impr/mo of equity migrating from calling.digital — captured on strengthened WD pages.
- [ ] Confirm the calling.digital→WD Change of Address is filed and the target pages exist on WD (depends on the GSC task on Michael's desk).
- [ ] IM: expand the ranking WD page(s) into the genuinely-best answer for those queries — answer-first intro (40–60 words), definition block, FAQ section, original stat or example, internal links to money pages.
- [ ] B&T: add FAQPage + Article schema; ensure lastmod bumps on publish so it re-crawls.
- [ ] Iris: visual gate.
- **Verify:** Polaris logs a position delta on those two queries within 4 weeks (target: page 5 → page 1–2) and a click increase off the same impressions.

### Task 1.2: Automotive Intelligence brand-authority defect
**Deliverable:** AvI ranking page 1 for its own brand name (currently pos ~23.7 for "automotive intelligence", 644 impr, 1 click — a defect, not a strategy problem).
- [ ] B&T: add Organization `sameAs` (LinkedIn/YouTube/GBP once it exists) + logo + founder to disambiguate from the same-named data firms.
- [ ] IM: tighten the homepage + /about to unambiguously own "Automotive Intelligence" + "run by people who sold cars" (the E-E-A-T hook).
- **Verify:** brand-name query moves to page 1 within 4 weeks; searchstack brand-name citation check returns the real brand.

### Task 1.3: AI Phone Guy indexation-but-zero-visibility
**Deliverable:** close the two real defects Polaris found — `/blog` "unknown to Google" and 10-day-stale interior crawl.
- [ ] B&T: ensure `/blog` is in the sitemap with lastmod and IndexNow-submitted; confirm interior lastmod is live.
- [ ] Polaris: re-run URL Inspection to confirm discovery.
- **Verify:** `/blog` moves from "unknown" to "submitted and indexed"; interior last-crawl date is current.

---

## Phase 2 — Best-answer content engine (owner: Internal Marketing writes / Iris gate / B&T ships / Polaris feeds)

Rationale: the highest-cited content types are comparison pages (~33% of citations) and definitive guides (~15%). We have zero comparison pages anywhere. Build the citation-worthy formats on the Phase-0 priority queries.

### Task 2.1: One comparison page per commercial brand
**Deliverable:** a fair, structured "X vs Y" / "best [category]" page per brand where it fits — e.g. Book'd vs AgencyBloc/EZLynx; AvI vs Fullpath/Numa/Matador; WD vs local agency alternatives.
- [ ] IM: build a real comparison table (criteria × options), balanced and honest — AI penalizes obviously biased comparisons and it violates our integrity line anyway.
- [ ] B&T: add `ItemList` + `FAQPage` schema; feed via sitemap/IndexNow.
- [ ] Iris: visual gate.
- **Verify:** page indexed + FAQPage valid (searchstack `schema`); citation check on the "best [category]" query re-run at +4 weeks.

### Task 2.2: One definitive guide per brand on its top query
**Deliverable:** the single best answer on the web for each brand's #1 priority query, answer-first, with statistics-with-sources (Princeton GEO: +37–40% citation lift) and a named author with credentials.
- [ ] IM: write to the agency content standard [[reference_agency_content_system_kit]]; lead with the direct answer; cite sources; add "last updated" date.
- [ ] B&T: Article + author schema; llms.txt regenerates from source (Polaris' generate-from-source pattern so it never goes stale).
- **Verify:** indexed + ranking delta on the target query; searchstack citation check re-run.

---

## Phase 3 — Third-party authority (owner: CMO + CRO + founder)

Rationale: ~6.5× of AI citations come from *off* your own domain. This is the biggest and slowest lever, and the one we have essentially none of. It cannot be automated or gamed — it is earned reputation.

### Task 3.1: Per-brand third-party target list
**Deliverable:** a ranked list of realistic citation surfaces per brand.
- [ ] Book'd → G2/Capterra/TrustRadius profiles (B2B SaaS review sites are heavily cited).
- [ ] Agent Empire → YouTube (@BuildAgentEmpire is already live — its only real citation surface) + Skool + relevant Reddit/Quora.
- [ ] AvI / AIPG → industry roundups, local press, Reddit trade communities, Quora answers with depth.
- [ ] WD → local/380-corridor press + partner mentions.
- [ ] Where legitimately notable, an accurate Wikipedia presence (only if it genuinely meets notability — never fabricated).
- **Verify:** list committed with an owner + first-target per brand.

### Task 3.2: Ship the first placement per brand
**Deliverable:** one real third-party placement per brand this cycle (a review-site profile, a genuinely useful Reddit/Quora answer, a YouTube video, a guest post, a roundup inclusion).
- [ ] Owner seat executes one placement per brand; founder opens doors where a human is required.
- **Verify:** placement live; searchstack citation-source tracking picks it up as a cited domain within the monitoring window.

---

## Phase 4 — Results / E-E-A-T (owner: CRO + Internal Marketing)

Rationale: real client wins are the most trustworthy authority signal and they compound. We have at least one converting client (P&P ranks pos ~4.9) and WD clients (Panda, Worden).

### Task 4.1: Case-study pages from real wins
**Deliverable:** one honest, specific case study per brand that has a real result — named client (with permission), the problem, what we did, the measurable outcome.
- [ ] CRO/IM: draft from real outcomes only (hero-metrics policy — no inflated numbers).
- [ ] B&T: Article/Review schema; link from money pages.
- **Verify:** page live + indexed; feeds the /results and comparison pages as proof.

---

## Phase 5 — Compounding loop (owner: Polaris → CMO)

### Task 5.1: Monthly authority scoreboard drives the next round
**Deliverable:** a monthly Polaris deliverable showing citation-rate and ranking deltas vs the Phase-0 baseline, with the next cycle's priorities.
- [ ] Polaris: diff searchstack citation counts + GSC positions month-over-month; log wins/losses to the learnings ledger.
- [ ] CMO: reads the scoreboard, re-prioritizes Phase 1–4 tasks for the next cycle.
- **Verify:** month-over-month citation count and page-1 ranking count both trend up; the loop selects the next targets from data, not guesswork.

---

## Self-Review (against the spec)

1. **Spec coverage:** the diagnosis was "seen not clicked → authority gap." Phase 1 captures buried equity (fastest click gain), Phase 2 builds citation-worthy content, Phase 3 builds off-domain authority (the 6.5× lever), Phase 4 adds proof, Phase 5 compounds and measures. All three ai-seo pillars covered. ✅
2. **Placeholder scan:** every task names a concrete deliverable, an owning seat, and a metric-based verification. No "TBD." The specific queries/positions are real (Polaris-verified). ✅
3. **Consistency:** owners match seats.yaml scopes (IM writes, Iris gates visuals, B&T ships code/schema, CRO owns revenue/results, Polaris measures, CMO prioritizes). Verification everywhere routes through the searchstack gauge + GSC — which is why the paired Polaris flag (wire in searchstack) is a hard dependency of Phase 0. ✅

**Hard dependency:** Phase 0 can't produce a citation baseline until the searchstack gauge is wired (paired flag to Polaris). Ranking-side baseline (GSC) is already live.

**The honest bottom line for the reader:** the last two months won the *findability* game. This plan is the *authority* game — slower, largely human, earned not gamed. That is what "Fifth-Avenue level" actually costs, and there is no tool that shortcuts it.
