# Persona Cron Loop — Phase A: KPI Scorecards

**Initiative owner:** Build & Tech
**Plan author:** B&T (was Infrastructure pre-merge)
**Date:** 2026-06-25
**Status:** PHASE A DRAFT — locked targets needed from Michael before Phase B starts
**Standing practice (per [[feedback_infrastructure_superpowers_standing_practice]]):** superpowers writing-plans → subagent-driven-development → verification-before-completion
**Unlocks:** [[project_avo_vision]] literal actualization — every persona becomes an accountable executive watching its own KPIs

## The architecture this serves

Michael's directive 2026-06-25:

> I don't want to work IN the business, I want to work ON the business. Personas must act like true Team Principal, Technical Director, Race Engineer, Chief Strategist — KPI-driven proactive professionals.

Translation: each persona is no longer a chat-responsive consultant. Each becomes an autonomous executive with:
- **Owned KPIs** (numeric, time-bounded, accountable)
- **Live scorecard** (auto-populated from source-of-truth systems)
- **Decision runbook** (if KPI X drops below target → here are my allowed levers)
- **Scheduled wake** (cron cadence matched to role — not on-prompt)
- **Adversarial review** (every AMBER/GREEN action gets refuted before ship)
- **Owner's Brief contribution** (one paragraph per persona, daily, KPI-status-led)

Michael's day compresses to: open Owner's Brief at 7:30am, see what 9 executives shipped overnight + what's RED-flagged for his call. The rest is work ON the business.

## Multi-phase plan

This PR ships ONLY Phase A — KPI definitions. Phases B/C/D follow in subsequent PRs.

| Phase | Deliverable | Days | Blocking gate |
|---|---|---|---|
| **A (this PR)** | Scorecard YAML per persona + this plan | 1-2 | Michael locks targets |
| B | Metrics collector service (GA4, GSC, Klaviyo, Loops, Instantly, Stripe, Twenty, Postgres, social) | 3-5 | A merged + targets locked |
| C | Persona cron loop v1 — CRO + CMO first | 2-3 | B merged + connectors verified |
| D | Fan out to other 7 personas | 5-7 | C verified clean for 5 days |

## The 9 personas + their executive roles

Per [[project_avo_slack.md]] (9 channels = 9 personas → opus-4-7) and the F1 analog Michael uses:

| Persona | F1 Analog | Scope |
|---|---|---|
| **Pit Wall** | Chief Strategist | Cross-portfolio strategic calls, persona-to-persona dependency resolution |
| **CMO** | Marketing Director | Marketing operating system, brand-marketing oversight, per-brand production lanes |
| **CRO** | Commercial Director | 3 B2B pipelines + Agent Empire + 3 CRMs ("CRO chat" scope) |
| **B&T** | Technical Director + Chief Engineer | Paperclip code, 22 agents, infrastructure (post-merge), vendor stack |
| **Internal Marketing** | Race Engineers (per car) | Production for AvI / CD / AIPG / AE / CustomerAdvocate (+ Book'd oversight) |
| **CMG** | Customer Engineer | Client Marketing Group — P&P focus, Miriam coordination, plans/photos/DKIM |
| **Agent Empire** | Junior Driver Program | B2C ops: Skool, YouTube, student experience, Riverside |
| **B2B Operations** | Track Operations | Post-sale delivery: AVO workflows, Miriam, Garrett GA, churn |
| **CustomerAdvocate** | Driver | WEND consumer product (NOVA + AATA), customer-facing |

## Scorecard YAML schema

Each persona owns a `services/persona_scorecards/<persona>.yaml`:

```yaml
persona: cmo
display_name: "Chief Marketing Officer"
f1_analog: "Marketing Director"
cron_cadence: "twice_daily"   # hourly | every_4h | twice_daily | daily | weekly
wake_times_cdt: ["07:00", "18:00"]

kpis:
  - name: weekly_qualified_leads_organic
    display: "Weekly qualified leads from organic channels"
    source: "ga4_twenty_join"   # named connector in metrics_collector
    source_query: "..."          # SQL / GA4 metric / API call spec
    target: 25
    target_unit: "leads/week"
    threshold_yellow: 18         # below target but above floor
    threshold_red: 12            # critical — escalate to Owner
    cadence: weekly
    owner_action_when_red: "..."
    levers:
      - "boost organic content velocity (spawn higgsfield_content_agent)"
      - "audit landing page conversion (spawn conversion_audit_agent)"
      - "redirect ad spend from cold to retargeting (CRO handoff)"

  - name: per_brand_search_volume_trend
    ...
```

The `levers` list is the executive's allowed action menu when a KPI goes red. Each lever maps to either an agent spawn, a flag-post to another persona, or a config change. The persona consults this menu via runbook, picks one, executes via APE.

## Per-persona draft scorecards (THIS PHASE)

Eight files land in `services/persona_scorecards/`:

- `pit_wall.yaml`
- `cmo.yaml`
- `cro.yaml`
- `bt.yaml`
- `internal_marketing.yaml`
- `cmg.yaml`
- `agent_empire.yaml`
- `b2b_operations.yaml`
- `customer_advocate.yaml`

**Target values are DRAFT v1 — based on inferred state from memory + telemetry. Michael's review is the load-bearing step before Phase B starts.** Wrong KPIs = wrong executives optimizing the wrong things, so we lock these together before any collector code ships.

## What this phase does NOT do

- Does NOT write the metrics collector (Phase B)
- Does NOT wire the persona cron loop (Phase C)
- Does NOT fire any agent on its own
- Does NOT mutate any production routing
- Does NOT touch APE Phase 1 (Infrastructure executor stays running, untouched)

Zero production impact. Pure design artifact.

## Standing practice for the rest of the build

Per Michael's 2026-06-25 directive: **all phases from here use superpowers suite as standing practice.**

- **writing-plans** — every phase opens with a plan PR like this one
- **subagent-driven-development** — implementer agent → spec reviewer agent → code quality reviewer agent for every non-trivial task (logged via `~/avo-telemetry/scripts/log_subagent.py` per [[feedback_subagent_dispatch_protocol]])
- **verification-before-completion** — claims gate through live verification; smoke-test against production before marking shipped
- **brainstorming** — co-design with Michael when KPI / target / lever choices are ambiguous

## Risk + tradeoffs (non-negotiable to acknowledge)

The moment personas have autonomous decision authority + budget to act on it, the system moves faster than Michael can supervise.

**Load-bearing guardrails:**
1. **Adversarial reviewer per persona** — every AMBER/GREEN action gets a second Claude session whose explicit prompt is "refute this." Quorum required before ship.
2. **RED-tier escalation** — any KPI miss above the red threshold stops the persona, emails Michael NOW, queues for explicit nod. Reuses [[project_secrets_management]] + APE Phase 1 patterns.
3. **Token budget guard** — per-persona daily ceiling. Burn-rate visible on the Owner's Brief. Higher Claude plan lifts the cap but doesn't remove the meter.
4. **Lever allowlist** — each persona can ONLY pull levers in its YAML. New levers require code review + Michael nod, not autonomous addition.

If those four break, autonomy → brand damage at scale. Treat them as core infra, not optional polish.

## Open questions for Michael (Phase A gate)

1. **9-persona list correct?** — does my list match the live AVO Slack channels? Any missing or recently sunset I should drop?
2. **CustomerAdvocate / WEND scorecard** — what KPIs matter? Driver scope is consumer-facing but I don't have its live metric surface in front of me. Worth a Michael-led brainstorm.
3. **Target values per KPI** — drafted at "reasonable from inference" but you know the actuals. Each YAML has a `# TBD-with-Michael` marker on targets that need your hand.
4. **Owner's Brief delivery cadence** — 7:30am only, or 7:30am + 6pm both? 6pm helps you see end-of-trading-day movement; 7:30am is the strategic morning read.
5. **Per-persona token budget** — fair starting daily cap per executive? Suggest CRO/CMO higher (revenue-driving), Infrastructure lower (steady-state), CustomerAdvocate variable (customer-facing burst).

## What ships in this PR

- This plan document
- 9 scorecard YAML files (one per persona) — drafted with `# TBD-with-Michael` markers on every target value
- No code paths, no service wiring, zero runtime impact

After review + target lock, Phase B writes the metrics collector and Phase C ships the first two persona cron loops (CRO + CMO).

## Rollback

Plan-only PR. Rollback = revert. Nothing depends on these YAMLs until Phase B/C land.
