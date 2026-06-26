# Persona Scorecards

Per-persona KPI definitions for the Persona Cron Loop architecture (see
`docs/superpowers/plans/2026-06-25-persona-cron-loop-phase-a-kpi-scorecards.md`).

Each persona is an accountable executive. The scorecard defines:

- **Owned KPIs** with targets, thresholds, source-of-truth
- **Cron cadence** matched to the role (hourly for revenue, every-4h for tech, daily for B2C ops)
- **Levers** the persona can pull when a KPI drops — maps to agent spawns, flag posts, or config changes
- **Token budget** ceiling per day per persona
- **Adversarial review** required flag (always true for autonomous personas)
- **Escalation email** for RED-tier breaches

Phase A (this batch) = scorecard definitions only. Phase B will write the
metrics collector that auto-populates current values against each KPI's source.
Phase C wires the persona cron loop. Phase D fans out from CRO+CMO to the
remaining 7.

**Target values marked `# TBD-with-Michael` need Michael's hand-on review
before Phase B starts.** Wrong KPIs = wrong executives optimizing the wrong
things, so target lock is the rate-limiting gate.

## Schema

```yaml
persona: <slug>                          # filename match, lowercase, underscore
display_name: <human readable>
f1_analog: <F1 role analog>
scope: <multi-line scope description>

cron_cadence: hourly | every_4h | twice_daily | daily | weekly
wake_times_cdt: [<24h time string>, ...] # CDT — matches APScheduler convention
adversarial_review: required             # second Claude session refutes before ship
escalation_email_red: <email>            # RED-tier breach email recipient

kpis:
  - name: <snake_case_metric_name>
    display: <human readable description>
    source: <connector slug>             # named connector in metrics_collector (Phase B)
    source_query: <optional query spec>  # SQL / API call / metric path
    target: <numeric or symbolic>        # MUST be hit
    target_unit: <unit string>           # for human readability
    threshold_yellow: <numeric>          # AMBER tier — surfaced in brief, not RED
    threshold_red: <numeric>             # RED tier — immediate escalation
    cadence: hourly | daily | weekly | monthly
    per_brand: [<brand_slugs>]           # optional — KPI tracked per brand
    levers:
      - "<lever description>"            # action persona is allowed to take
      - ...
    owner_action_when_red: <optional>    # specific override for RED behavior

token_budget_daily_usd: <numeric>        # daily Claude spend ceiling for this persona
```

## Persona roster (9)

| File | Persona | F1 Analog | Cadence |
|---|---|---|---|
| pit_wall.yaml | Pit Wall | Chief Strategist | twice_daily |
| cmo.yaml | Chief Marketing Officer | Marketing Director | twice_daily |
| cro.yaml | Chief Revenue Officer | Commercial Director | hourly |
| bt.yaml | Build & Tech | Technical Director + Chief Engineer | every_4h |
| internal_marketing.yaml | Internal Marketing | Race Engineers (per car) | twice_daily |
| cmg.yaml | Client Marketing Group | Customer Engineer | daily |
| agent_empire.yaml | Agent Empire | Junior Driver Program | daily |
| b2b_operations.yaml | B2B Operations | Track Operations | daily |
| customer_advocate.yaml | Customer Advocate (WEND) | Driver | daily |

## How to review (Michael)

For each YAML:

1. **Are the right KPIs listed for this executive?** Add/remove KPIs that don't match what you want this seat accountable for.
2. **Are the targets realistic?** Every `# TBD-with-Michael` marker needs a real number, or a "set after baseline week" comment.
3. **Are the levers sufficient?** If a KPI goes red, can the persona actually move it with the listed actions? Anything missing means the executive will spin and escalate to you instead of executing.
4. **Is the cadence right?** Hourly may be too aggressive for some, daily too sparse for others.
5. **Token budget per persona?** Suggest setting after we see Phase C wake-cycle costs measured.

Once locked, Phase B (metrics collector) builds against this contract.
