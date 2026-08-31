# AvI Truth-Capture — Phase 0 (the moat)

**Date:** 2026-08-31 · **For:** Michael (20 min, in your own words) · **Feeds:** `automotive-intelligence-site/src/data/truth/avi_facts.yaml` → the citability matrix engine.

Carvana's moat is not 6,700 pages, it is the real data behind them. Ours is **your floor**. No AI vendor
or consultant can fabricate what an active dealer at the #1 volume Chevy store actually knows. This
captures that as sourced facts the engine renders into pages AI must cite. **Rule: only real things you
actually know or have seen. If you're not sure, skip it. We never invent a specific.**

## How a captured answer becomes a fact
Each answer you give becomes one or more rows:
```yaml
- claim: "In DFW, a shopper can reach a dozen comparable stores before lunch, so first useful reply usually wins the test drive."
  source: "operator — Michael Rodriguez, active DFW dealer"
  tags: [dfw-market, speed-to-lead]
  entity: dallas   # links it to the /ai/dallas-dealerships page
```
`source: operator` is a REAL, attributable E-E-A-T source (a named active dealer), not an unverified stat.
That is what makes it citable and honest at the same time.

## The interview (answer any/all, in plain talk)
**Market truth (per DFW area) — the stuff only someone on the floor knows:**
1. What actually wins a deal in Dallas vs Fort Worth vs the suburbs? Where does speed matter most?
2. What is genuinely different about how buyers shop each area (Plano/Frisco vs Arlington vs Denton)?
3. Which OEM brands behave differently in this market, and how (Chevy vs Ford vs Toyota buyers/process)?

**What vendors get wrong (the "kill the mistake" material — highest citation value):**
4. What is the most common AI/vendor claim you know is oversold, and why, from real experience?
5. What do most stores *think* their problem is vs what it actually is?
6. What is a "30% lift" number you've seen that fell apart when you asked "lift on what base?"

**Real operations truth:**
7. Where does a deal actually leak between the DMS, CRM, and service tools? Give a concrete example.
8. What does a real after-hours or slow-response miss cost a store, in your experience (ranges, not made-up exacts)?
9. What is the single highest-return, least-sexy AI move you've actually seen work in a store?

**Proof / authority:**
10. What have you personally built or fixed on a real floor that you'd stake your name on?
11. What would you tell a GM friend over coffee that you'd never put in a vendor deck?

## What happens next
Your answers → `avi_facts.yaml` (each tagged to a market/brand/problem) → the matrix engine writes each
`/ai/{slug}` page as the *best, truest* answer to that query, grounded only in your facts, signed under
your name. Pages with no real fact behind them do not get written (the truth-bank gate). Then we measure:
do these pages start getting cited, and do the "who is Automotive Intelligence" errors disappear.

**This is the difference between a page generator and the cited authority in DFW dealer-AI. It only works
with your real input — that is the point, and it's why no competitor can copy it.**
