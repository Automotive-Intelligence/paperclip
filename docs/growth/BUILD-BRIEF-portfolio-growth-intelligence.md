# Build Brief / Kickoff Prompt — Portfolio Growth Intelligence

**Issued by:** Michael Rodriguez, Team Principal · **Date:** 2026-06-28
**Working name:** "The Strategist" (F1 race strategist — owns the telemetry and makes the calls). *Final name is mine to set — see naming note at bottom; treat the name as a variable `<SEAT>` until I confirm.*
**Status:** Kickoff prompt. Do NOT start building until you have produced and I have approved the plan (Phase 0 below).

---

## 0. How to run this build (READ FIRST)

You are building an autonomous **Portfolio Growth Intelligence** for the AVO garage. Before writing a
line of code:

1. **Open with `superpowers:writing-plans`.** Produce the full implementation plan — current state,
   design, phased steps with verification, risks, rollout — and post it for my approval. This mirrors
   our standing infra practice (PRs open with `superpowers:writing-plans`). Do not skip to building.
2. **Honesty-first, always.** Everything you build inherits `config/principles.py` →
   `foundation_header()` and the **hero-metrics policy**: never fabricate a number. If a metric is
   unavailable, the output says **"unknown — needs check,"** never a guess dressed as fact.
3. **Don't push to `main` without my sign-off.** Feature branch + PR. Use the `gh`/`railway`/`vercel`/
   `doppler` CLIs yourself rather than asking me to click dashboards.
4. **Wire, don't stub.** Where I say "wire X," I mean connect it for real and prove it returns live
   data, or escalate the specific blocker (access/entity/cost) to my desk. A scoreboard over empty
   dashboards is theater — that is the exact failure we are fixing.

---

## 1. Why this exists (mandate)

The portfolio SEO/AEO audit (`docs/seo/`, 2026-06-28) proved our eye isn't on the ball: **0 of 5**
tested buyer queries cited any of our brands, **no Google Business Profile** exists for any local
brand, and **Core Web Vitals is unmeasured on all six sites**. We have no standing growth scoreboard
and no seat that owns it.

`<SEAT>` exists to **own the scoreboard for the entire garage** — to measure every growth-driving
metric across all brands, analyze it, learn from it, decide what matters, and hand off the work to the
seat that should execute it. It reports up to the **CMO** and the **Team Principal**. It is delivered
as a **scheduled, pay-per-run intelligence** (not an always-on Slack persona) so it has a mind of its
own without an idle meter running — the same fan-out model that produced the SEO audit itself.

**The one number it serves:** MRR (the garage scoreboard, per `principles.py`). Everything `<SEAT>`
measures must ladder up to "did this grow recurring revenue for a client, or the pipeline that
produces it?"

---

## 2. Scope — what it owns and what it must NOT drift into

**Owns:** cross-brand growth measurement; SEO/AEO/analytics intelligence; anomaly + delta detection;
prioritization; the weekly scoreboard; the learnings ledger; handoff dispatch.

**Does NOT own (it commissions these seats, never does their job):**
- Copy / positioning / content writing → **Internal Marketing** (per brand).
- Visual / creative / landing-page design → **Iris** (and her visual gate is mandatory before any
  visual/web change merges — see `[[feedback_iris_visual_gate_before_merge]]`).
- Revenue / funnel / sales enablement ownership → **CRO** (Revenue & Sales).
- Technical site changes, schema, redirects, code → **Build & Tech**.
- Cross-portfolio strategy calls → **Pit Wall / Team Principal**.
- Vendor/identity/secrets/domains → **CTO**.

`<SEAT>` produces the *intelligence and the priority*; the owning seat produces the *change*. Respect
scope; hand off, don't drift (`[[feedback_respect_persona_scope]]`).

---

## 3. Instrumentation to wire (the missing substrate — wire ALL of it)

This is the core of the build. The audit ran half-blind; `<SEAT>` cannot. Wire every row, or escalate
the named blocker. Secrets go to **Doppler** (SoT) and sync to Railway — never paste
(`[[project_secrets_management]]`, use `--silent` on set).

| # | Data source | What it gives us | Current state (2026-06-28) | Action to wire | Owner/secret |
|---|---|---|---|---|---|
| 1 | **Google Search Console API** | True indexation, query/CTR/position, CWV field data, coverage errors | **Not connected** — audit inferred from `site:` | Connect all brand properties; service-account or OAuth; daily pull | `GSC_*` in Doppler; CTO provisions access |
| 2 | **PageSpeed Insights API** | Lab CWV (LCP/INP/CLS) mobile per page | **Keyless quota 429'd on all 6** | Get a PSI API key; wire keyed pulls | `PSI_API_KEY` |
| 3 | **GA4 (all brands)** | Sessions, sources, conversions, funnel | Partial — `cd-ops/pull_ga4.py` exists, multi-property by flag, service-account | Extend to every brand property; unify into one pull | reuse `cd-ops` SA |
| 4 | **Google Business Profile** | Local pack presence, reviews, NAP, calls/directions | **None for any local brand** | Decide create/claim per brand; wire GBP API where entity allows. **DEPENDENCY:** AIPG has no LLC, CD LLC expired — GBP is gated on entity hygiene `[[project_entity_hygiene]]`. Escalate the gate to my desk | CTO + my call |
| 5 | **Rank tracking (commercial + local)** | Position over time for money keywords per brand/city | None | Build a weekly rank snapshot via **keyapi SERP** (`[[reference_keyapi_ai]]`) for the keyword maps in `docs/seo/` | `keyapi` key |
| 6 | **AI-citation monitoring** | Share-of-voice in ChatGPT/Perplexity/Gemini/AI Overviews | None — and we're at 0/5 | DIY now: scripted query panel via keyapi + WebSearch, logged weekly. Evaluate a paid tool (Otterly/Peec/ZipTie) at month 2 if DIY is too noisy | `keyapi`; paid TBD |
| 7 | **Conversion / funnel + attribution** | Form fills, calls, demo books, email→pipeline | Scattered (GA4 events, Loops/Instantly, phone) | Map the funnel per brand; define the conversion event each site must fire; reconcile with CRM (Twenty) | with CRO + B&T |
| 8 | **Time-series store** | Week-over-week deltas (the whole point of a scorecard) | None — markdown telemetry only | Stand up a structured metrics store (SQLite or Postgres table) + a `growth_state.md` telemetry file in `avo-telemetry` for the human-readable layer | with CTO |
| 9 | **Bing Webmaster (Copilot surface)** | Bing index + Copilot citation context | None | Optional / Phase 3 | low priority |

If any source can't be wired (access, cost, entity), **`<SEAT>` reports it as a named gap on the
scorecard with the blocker and the owner** — it does not silently drop coverage
(`[[feedback_blog_standard_signal_not_noise]]` discipline applies to its own measurement).

---

## 4. Autonomy loop — give it a mind of its own

Every scheduled run executes this loop. This is what makes it analyze, learn, and decide rather than
just dump numbers.

1. **ANALYZE** — fan out per brand (the audit's proven pattern: parallel sub-agents, one per brand),
   pull all wired sources, compute deltas vs the time-series store, and run anomaly detection (traffic
   drop, ranking loss, CWV regression, citation gained/lost, conversion dip).
2. **LEARN** — maintain a **learnings ledger** (`growth_learnings.md`, modeled on the intent-data
   learnings ledger `[[reference_intent_data_campaign_template]]`): every commissioned change is logged
   with a hypothesis and revisited after N weeks to record whether it moved the metric. Over time this
   becomes a causal "what works per brand" knowledge base it reasons from. It also **ingests** the
   `Foundation Bible` and the SEO/AEO skills each run so its standard never drifts
   (`[[reference_foundation_bible]]`).
3. **DECIDE** — rank findings by impact × effort (same scale as the audit), apply the **moral gate**
   (`evaluate_action_morally` in `principles.py`) and hero-metrics policy, and classify each into:
   auto-handoff (clear owner, bounded), needs-pod (cross-functional), or escalate-to-Michael (my hands
   required, or any harm/ambiguity flag).
4. **HANDOFF** — open a flag for each actionable finding via our flag protocol
   (`🏁 FLAG FOR: <seat>` → flag-router → Slack ping, `[[project_flag_router]]`), routed through
   `seats.yaml` (`[[reference_seats_yaml]]`). Log substantive dispatches with
   `~/avo-telemetry/scripts/log_subagent.py` `[[feedback_subagent_dispatch_protocol]]`; close its own
   flags with `close_flag.sh` `[[feedback_close_flag_protocol]]`.
5. **ESCALATE** — anything needing my hands goes to "Michael's desk" in the Team Principal
   two-questions format (shipping? revenue?) — terse, decision-forcing, queued.

**Bounded authority (this is the autonomy contract — set it explicitly in the plan):** `<SEAT>` may
*decide and dispatch* recommendations and *commission* the owning seat. It may NOT itself ship code,
copy, creative, DNS, or spend. Anything that ships still passes the owning seat's gate (e.g. Iris's
visual gate, B&T's deploy, CRO's revenue call). Mirror the Persona-Autonomy-v2 lesson: flipping a mode
flag ≠ execution; authority is bounded by `can_ship` and the gates stay intact
(`[[project_persona_autonomy_v2]]`).

---

## 5. Pod & handoff wiring (Iris / CRO and beyond)

`<SEAT>` must be **pod-capable** — able to convene or join a working group when a finding is
cross-functional. Wire this from the start:

- **Registry:** add `<SEAT>` to `seats.yaml` (canonical + aliases + Slack channel) so cross-chat
  routing resolves to it `[[reference_seats_yaml]]`.
- **Default pods:**
  - **Growth × Iris** — any finding whose fix is visual/creative/landing (hero imagery, CWV from
    unoptimized media, landing-page CRO). `<SEAT>` produces the data + the ask; Iris owns the creative
    and the **visual gate before merge**. Iris is visual-only — don't route copy to her
    `[[feedback_iris_visual_only]]`.
  - **Growth × CRO** — any finding about funnel, conversion, pipeline, or sales enablement. `<SEAT>`
    surfaces where traffic isn't converting; CRO owns the revenue motion `[[project_revenue_sales_scope]]`.
  - **Growth × Build & Tech** — technical SEO, schema, redirects, the portfolio "fix once, roll to
    all" items from the audit.
  - **Growth × CMO** — strategy, prioritization conflicts, and the auto-publish gates
    `[[project_cmo_operating_system]]`.
- **Pod mechanism:** a shared Slack channel (e.g. `#growth-pod`) where `<SEAT>` posts the scorecard and
  @-tags the relevant seats; convened automatically when a finding is tagged `needs-pod` in DECIDE.
  Reuse the avo-slack persona-channel pattern `[[project_avo_slack]]`.
- **Make it available to the others:** other seats can *query* `<SEAT>` (pull the latest scorecard /
  ask "how is brand X trending?") via the same routing — it's a shared instrument panel, not a silo.

---

## 6. Cadence (schedule)

Wire via our scheduling backbone (the `schedule` skill / cloud routine, matching the **AvI always-on
engine** pattern `[[project_avi_always_on_engine]]`; coordinate server-side cadence with APScheduler
in Paperclip where a data pull must run there). Times in CDT, aligned to existing Pit Wall cadence.

- **Daily (light watchdog):** indexation breaks, CWV regressions, ranking cliffs, citation
  gained/lost, conversion anomalies → alert only if something moved. Can piggyback the 7:30 AM CTO
  sweep window.
- **Weekly (Mon AM):** full portfolio scorecard deliverable + deltas + decisions + handoff queue.
- **Monthly (first Mon):** deep-dive — AI-citation share-of-voice trend, keyword/content gap movement,
  learnings-ledger review (what moved metrics, what didn't).
- **Quarterly:** re-run the full SEO/AEO audit fan-out (`docs/seo/`) to re-baseline.

---

## 7. Outputs

- **`growth_state.md`** (avo-telemetry) — the living human-readable scoreboard, session-start readable
  by other seats.
- **Dated weekly deliverable** → `docs/growth/SCORECARD-<YYYY-MM-DD>.md`: portfolio table (per-brand
  grades + the metric deltas + the one number), per-brand drill-downs, **handoff queue** (every flag
  opened + owner), and **Michael's desk** (escalations, two-questions format). Scannable,
  decision-forcing, paste-ready — no exposition (CTO deliverable discipline).
- **`growth_learnings.md`** — the causal ledger described in §4.
- Every claim tagged **[Verified]** / **[Inferred]** / **[unknown — needs check]**, same as the audit.

---

## 8. Guardrails (non-negotiable)

- **Hero-metrics policy** — no fabricated numbers, ever `[[feedback_hero_metrics_policy]]`.
- **Iris visual gate** before any visual/web change merges `[[feedback_iris_visual_gate_before_merge]]`.
- **No em-dashes** in any outbound/brand-facing copy it drafts `[[feedback_no_em_dashes_general]]`.
- **Manual DNS only** if any finding touches DNS `[[feedback_manual_dns_only]]`.
- **Don't push main / don't `/clear` persona chats** — recommend "run your session start protocol."
- **Respect scope; hand off, don't drift.**
- **Moral gate** before any consequential action; escalate on any harm/ambiguity flag.

---

## 9. Runtime / build shape (recommended — confirm in the plan)

- **Brain:** a scheduled **claude.ai cloud routine** running the §4 loop with `superpowers` enabled
  (pay-per-run autonomy, matches AvI always-on engine).
- **Hands:** a Paperclip module `tools/growth/` for the data pulls (GSC/PSI/GA4/keyapi/citation),
  callable both by the routine and by APScheduler.
- **Persona file:** `services/personas/<SEAT>.md` (mirrored into avo-slack) so it has identity +
  `foundation_header()` injected, consistent with every other seat.
- **Secrets:** Doppler → Railway. **Storage:** metrics table (SQLite/Postgres) + `growth_state.md`.

---

## 10. Phased rollout (each phase ends in a verification I can check)

- **Phase 0 — Plan (`superpowers:writing-plans`).** Full plan + this brief reconciled; access/entity
  blockers surfaced to my desk. *Verify: I approve the plan.*
- **Phase 1 — Wire the substrate (§3).** Connect GSC, PSI key, GA4-all-brands, keyapi rank + citation
  panel, time-series store. *Verify: one manual run returns live data for all 6 brands with named gaps
  for anything blocked.*
- **Phase 2 — Read-only scorecard.** Weekly deliverable + `growth_state.md`, deltas working. No
  handoffs yet. *Verify: a scorecard I'd actually act on, all claims tagged.*
- **Phase 3 — Decisions + handoffs.** DECIDE gate + flag dispatch + learnings ledger live. *Verify: a
  real finding routed to the right seat and closed.*
- **Phase 4 — Pod + bounded autonomy.** `seats.yaml` + `#growth-pod` + Iris/CRO/B&T/CMO wiring;
  bounded auto-dispatch within the authority contract. *Verify: a cross-functional finding convenes a
  pod and produces a shipped change through the owning seat's gate.*

---

## 11. Definition of done

`<SEAT>` runs on schedule, pulls live data for all six brands (gaps named, not hidden), produces a
weekly scoreboard with week-over-week deltas, decides priorities under the moral + hero-metrics gates,
opens correctly-routed handoffs to CMO/Iris/CRO/B&T, convenes pods when cross-functional, maintains a
learnings ledger that demonstrably informs later decisions, and escalates only what needs my hands —
all without fabricating a single number.

---

### Decisions I defaulted (override any before Phase 0)
1. **Name** — working name "The Strategist." You named Iris; this one's yours to name too.
2. **Brain = scheduled claude.ai cloud routine** (pay-per-run) rather than an always-on Slack persona —
   matches your cost concern and the AvI engine pattern. Say the word if you want it as a full standing
   persona instead.
3. **AI-citation tracking = DIY (keyapi + WebSearch) first**, paid tool (Otterly/Peec/ZipTie) only if
   month-2 noise warrants the spend.
4. **Default pod members = Iris + CRO + Build & Tech + CMO.** Add/remove any.
5. **GBP is gated on entity hygiene** — I've routed that to your desk as a blocker, not assumed it away.
