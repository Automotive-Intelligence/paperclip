# Persona Cron Loop — Phase C: Persona Wake + Adversarial Reviewer + Owner's Brief

**Initiative owner:** Build & Tech
**Phase:** C (Phase A scorecards PR #70 awaiting target lock; Phase B1a #80 merged; #91/#92/#93 pending merge)
**Date:** 2026-06-27
**Status:** PLAN DRAFT — needs greenlight before any persona-wake code lands
**Standing practice:** writing-plans → subagent-driven-development → verification-before-completion

## What Phase C produces (the actual AI-native business)

After Phase A (scorecards) + Phase B (snapshots), Phase C turns each persona from "config + data" into an autonomous executive that wakes on cadence, reads its own KPIs, decides what to do, executes, adversarially reviews, and briefs Michael — all without him typing a word.

This is the literal AVO vision per [[project_avo_vision]]: every build reduces Michael's time-in-loop. Phase C reduces it from "respond to whatever pops up" to "read Owner's Brief, make RED-tier calls, work ON the business."

## The three pieces

### 1. Persona Wake Loop (`services/persona_wake.py`)

Each persona gets a scheduled wake driven by its scorecard's `cron_cadence` field. On wake:

```
1. Load scorecard (services/persona_scorecards/<persona>.yaml)
2. Load decision runbook (services/persona_runbooks/<persona>.yaml — NEW)
3. Pull latest snapshot per KPI (and per-brand) from kpi_snapshots
4. Classify each KPI: green / yellow / red against threshold_yellow + threshold_red
5. For each non-green KPI:
     a. Match against runbook: "if KPI X status is Y, candidate levers are L1..Ln"
     b. Spawn an internal-decision Claude session: "given these levers + this context,
        choose action OR escalate to RED"
     c. If proposed action's reversibility is RED: queue as RED for Owner's Brief, do NOT execute
     d. Else: adversarial reviewer (see #2). If quorum, execute. If refuted, queue as RED.
6. Compose persona's contribution to Owner's Brief — one paragraph:
     "{persona}: {N green / M yellow / K red KPIs}. This window I: {actions taken}.
      What's still RED for your call: {bullets}. KPI movers: {top 3 deltas vs last cycle}."
7. Write a wake_event row to `persona_wake_events` (NEW table) for audit
8. Insert brief contribution into `owner_brief_queue` (NEW table)
```

The wake loop reuses existing infrastructure:
- APScheduler for the cadence (the same pattern as APE's `ape_persona_executor_tick`)
- `agent_handoffs` for execution spawn (already wired by APE Phase 1)
- `kpi_snapshots` from Phase B for the read-side

### 2. Adversarial Reviewer (`services/adversarial_reviewer_v2.py`)

The APE Phase 1 reviewer (`services/adversarial_reviewer.py`) exists but is scoped to ship audits. v2 generalizes to per-persona-wake action review.

Contract:
```
def review(proposed_action: dict, persona_context: dict, kpi_context: dict) -> ReviewVerdict
```

The reviewer is a fresh Claude session prompted to REFUTE the action. Verdicts:
- `APPROVE`  — action ships immediately
- `REVISE`   — original session gets the refutation, re-proposes (max 2 iterations)
- `HALT`     — escalates to RED for Owner's Brief

Quorum rule (per the AVO vision risk callout): for AMBER-tier actions, 2-of-3 reviewers required. For GREEN, 1 reviewer. RED never executes autonomously.

### 3. Owner's Brief (`services/owners_brief.py` + email template)

The single most important user-facing output of the whole initiative.

```
DAILY 7:30 AM CDT (and optional 6 PM end-of-day):

  ╭──────────────────────────────────────────────────────────╮
  │  OWNER'S BRIEF — 2026-06-27 07:30 CDT                    │
  │  Portfolio: 73 green · 12 yellow · 4 RED                 │
  ╰──────────────────────────────────────────────────────────╯

  🚨 RED — needs your call (4)
    • CRO: pipeline_coverage_ratio dropped to 1.4x on AvI (red threshold 1.5).
           Adversarial reviewer halted the proposed campaign-volume bump pending
           Michael nod (would 2x Instantly send rate; reversibility AMBER).
    • B&T: deploy_frequency = 0/week (red threshold 1). 2 PRs stalled in review.
           Reviewers (sessions): Claude-cycle-A, Claude-cycle-B.
    • CMG: client_response_time = 96h on Miriam ping (red threshold 72h).
           No autonomous response — Michael's voice required.
    • Pit Wall: portfolio_health_index = 48 (red threshold 50).

  ✅ Shipped this window (12 — GREEN tier, auto-shipped)
    • CMO: rotated underperforming Instantly campaign (reply rate < 1%)
    • B&T: closed RESOLVED flag for B&T errors (false-positive detector bug)
    • Internal Marketing: WD blog post drafted + queued for Tuesday
    • ... [9 more]

  📊 KPI movers (top 3 deltas vs last cycle)
    • cro.new_opportunities_per_week AvI: 25 → 30 (+5)
    • bt.api_uptime_per_service: 100 → 100 (steady)
    • bt.domain_ssl_green_rate: 100 → 100 (steady)

  ⏸️ Stalled lanes
    • Phase A scorecards (PR #70) awaiting target lock — 30 TBD-with-Michael markers
    • Twenty Book'd workspace key — pending Ryan
```

Delivery via existing Resend integration (per `services/ape_audit_email.py`).
Reply-via-email loop wired through `services/ape_reply_parser.py`: Michael can
respond with "approve", "halt", or free-form notes that get attached to the
relevant RED items.

## Required new tables (migrations land in this PR)

```sql
CREATE TABLE persona_wake_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    persona         TEXT NOT NULL,
    wake_ts         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cadence         TEXT NOT NULL,
    kpis_evaluated  INTEGER NOT NULL,
    green_count     INTEGER NOT NULL,
    yellow_count    INTEGER NOT NULL,
    red_count       INTEGER NOT NULL,
    actions_taken   JSONB,
    duration_ms     INTEGER,
    error_detail    TEXT
);

CREATE TABLE owner_brief_queue (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    persona         TEXT NOT NULL,
    brief_window    TEXT NOT NULL,     -- 'morning' | 'evening'
    contribution    TEXT NOT NULL,     -- the persona's paragraph
    red_items       JSONB,             -- structured RED-tier items for the top of fold
    shipped_items   JSONB,             -- what GREEN-shipped this window
    kpi_movers      JSONB,             -- top 3 deltas vs last cycle
    ts_added        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ts_sent         TIMESTAMPTZ,
    UNIQUE(persona, brief_window, DATE(ts_added))
);
```

## Decision runbook YAML schema (new per-persona file)

Per `services/persona_runbooks/<persona>.yaml`:

```yaml
persona: cmo

rules:
  - when_kpi: weekly_qualified_leads_organic
    when_status: yellow
    candidate_levers:
      - id: boost_content_velocity
        action: spawn_agent
        target_agent: higgsfield_content_agent
        payload_template:
          brand: "{worst_brand}"
          mode: catch_up
        reversibility: AMBER
        impact: ROUTINE
      - id: audit_landing_conversion
        action: spawn_agent
        target_agent: conversion_audit_agent
        reversibility: GREEN
        impact: ROUTINE
    selection_prompt: |
      Given the current KPI value vs trend, which lever is most likely to
      move organic-lead volume this week? Prefer GREEN-reversibility levers
      if confidence is moderate; AMBER if signal is strong; RED if you would
      need Michael to approve a budget shift.

  - when_kpi: weekly_qualified_leads_organic
    when_status: red
    candidate_levers:
      - id: emergency_owner_consult
        action: escalate_to_red
        message: "WD organic lead volume cratered (current: {value}, target: {target}). Need urgent strategy session."
```

`selection_prompt` is what the persona's decision-Claude session sees alongside the levers. Keeps decisions in YAML reviewable + auditable.

## Phased ship within Phase C

| Sub-phase | Deliverable | Days | Gate |
|---|---|---|---|
| **C1** | Tables migration + persona_wake.py skeleton + 1 persona (B&T — lowest-risk) | 2 | Michael greenlight on this plan |
| **C2** | Adversarial reviewer v2 + B&T integration | 1 | C1 verified 24h clean |
| **C3** | Owner's Brief composer + delivery (no persona expansion) | 2 | C2 verified clean |
| **C4** | Add CRO + CMO personas (revenue-driving expansion) | 2 | C3 brief landing in inbox cleanly |
| **C5** | Fan out remaining 6 personas (Pit Wall, Internal Marketing, CMG, Agent Empire, B2B Operations, Customer Advocate) | 3-4 | C4 verified 5 days |

Total wall-clock for Phase C: ~10-12 days assuming clean verification at each gate.

## Risk callouts (non-negotiable)

1. **Adversarial reviewer is the load-bearing safety net.** If it breaks, autonomy becomes unsupervised AI. C2 includes explicit "reviewer is down → persona wakes but does NOT execute any AMBER/GREEN action; only RED queues" failsafe.

2. **Token budget guard per persona.** Each persona has a daily_token_budget_usd in its scorecard (Phase A). The wake loop checks against actual Anthropic usage (via b1b's `anthropic_usage_api` connector if shipped) before each wake. Over-budget personas pause and post a RED flag.

3. **Loop runaway.** A persona that produces 100 actions per wake would burn budget + saturate agent queues. v1 cap: max 5 actions per persona per wake. v2 tunes per persona based on observation week data.

4. **Schema drift across phases.** Phase C reads `kpi_snapshots.status` taxonomy from Phase B. If we add new states, both layers must agree. Document the union as the contract.

5. **Brief deliverability.** Email is a single point of failure for the Owner-visible surface. C3 ships a Slack DM fallback so Michael gets briefed even if Resend is down.

## Open questions for Michael

1. **Owner's Brief delivery cadence** — 7:30am only, or 7:30am + 6pm? (Recommend both.)
2. **Brief delivery channel** — michael@worshipdigital.co or michael@automotiveintelligence.io? Both? (Memory says both are active.)
3. **First persona to come online** — recommend B&T (lowest revenue-touching; safest learning loop). Alternatives: CMO (highest leverage but high stakes), Pit Wall (highest portfolio-visibility but most adversarial-review pressure).
4. **Adversarial reviewer count for AMBER** — recommend 2-of-3 quorum. Higher = safer but slower + more expensive.
5. **Action cap per wake** — 5 is a safety guess. You may want it higher for CRO during launch windows, lower for CMG.

## What ships in this PR

Just this plan. Zero code. Zero runtime impact.

After greenlight, C1 lands as its own PR with: migration, persona_wake.py framework, B&T wake config, decision runbook YAML for B&T, APScheduler entry.

## Rollback

Plan-only. Revert = revert.
