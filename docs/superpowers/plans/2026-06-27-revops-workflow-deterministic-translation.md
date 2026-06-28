# RevOps Workflow Fix — Deterministic Translation of Agent Prompts

**Owner:** Build & Tech
**Date:** 2026-06-27
**Triggering flag:** CRO RED 2026-06-27T21:45Z — Brenda/Darrell/Randy agent prompts are dead code at runtime. PRs #72/#74/#75 (prompt retrains) decorative.
**Status:** PLAN DRAFT — needs greenlight before any workflow rewrite ships
**Standing practice:** writing-plans → subagent-driven-development → verification-before-completion

## The bug, in one paragraph

`brenda_run()` (and `darrell_run()`, `randy_run()`) in `rivers/calling_digital/workflow.py` execute deterministic Python — `_find_new_contacts()`, `_score_and_enroll()`, etc. — and return None. The post-run hook in `app.py:546` receives `raw_output=None` and falls back to writing `f"{agent_name} run completed at {timestamp}"` as the log. The CrewAI Agent objects defined in `agents/<river>/<name>.py` (which carry the updated scoring rubric, output discipline rules, iron rules) are never invoked. **Every prompt edit since the agents were created has had zero runtime effect.**

Symptom: `agent_logs.content` for these three is always a ~55-char heartbeat. Randy's leak grew 21→29 in 2 days because no enrollment is firing despite "successful" runs.

## Decision: Option B (translate prompts to Python)

CRO surfaced two options. Choosing B.

**Option B — translate prompt rules into workflow.py Python directly.**

Why B over A (wire workflow.py → agent.run()):
- The prompt rules ARE deterministic (territory ladder, vertical match, score thresholds, send schedules). Letting Claude re-derive them per contact is slow + expensive + introduces variance the org doesn't want.
- A real per-run summary >=200 chars can be generated in Python from the run's tallies (scored: N, Track A: M, Track B: K, sequences fired: J, hot leads escalated: L) — no LLM needed.
- The agent_logs.content the org consumes is operational telemetry, not creative output. Python composes it deterministically.
- Option A would still need Option B's tally generation to make the LLM's output useful to CRO sweeps.

**Where prompts SHOULD stay in agent.run() — copy generation (Track A/B email body), hot-lead alert phrasing, ICP-specific outbound writing.** Those are creative tasks. The dispatch + scoring + scheduling are deterministic.

## Scope of this initiative

Three agents to fix, in priority order (CRO's call):

| Agent | River | Current state | Daily leak |
|---|---|---|---|
| **Brenda** | calling_digital | Heartbeat only, no scoring against new rubric, no Loops vertical schedule | Unknown but compounding |
| **Randy** | aiphoneguy | 29 GHL contacts unenrolled, leak +8 in 2 days | 4/day average |
| **Darrell** | automotiveintelligence | Heartbeat only, scope unclear at workflow.py level | Unknown |

Plus a discovery item:
- **Marcus** — PR #72 (territory retrain) shows 0 hits in 380, 0 in DFW, 6 national. Either his agent IS being called somewhere ignored by CRO, OR his Python is also dead-code with KEYAPI tools returning national-skewed results. **Verify before claiming Marcus is fine.**

## Phased ship

| Sub-phase | Deliverable | Days |
|---|---|---|
| **R1** | Brenda — translate scoring rubric, vertical send schedule, output discipline. Verify with live test run. | 1-2 |
| **R2** | Randy — translate enrollment path, GHL fix, leak closure. Re-enroll the 29 stuck contacts. | 1 |
| **R3** | Darrell — same pattern as Brenda. | 1 |
| **R4** | Marcus verification — trace KEYAPI tool behavior, confirm or fix territory filter. | 0.5-1 |

Each sub-phase = one PR. Subagent spec + quality review per standing practice.

## What goes into each workflow rewrite

Pattern, taking Brenda as the example. Apply same shape to Randy + Darrell.

**1. Move scoring rubric into Python:**
```python
TERRITORY_SCORES = {
    "380_corridor": 3,  # Prosper, Celina, Aubrey, Little Elm, Pilot Point, Frisco-adjacent
    "greater_dfw":  2,  # Dallas, Plano, McKinney, Frisco, Denton, Arlington, Fort Worth
    "tx_outside":   1,
    "national":     0,
}

VERTICAL_BONUS = {
    "med_spa": 3, "pi_law": 3, "real_estate": 3, "home_builder": 3,
}

REVENUE_BONUS_USD = 1_000_000  # +2 if known revenue exceeds
ENGAGEMENT_BONUSES = {"ai_interest": 2, "referred": 2, "engaged": 1}

TRACK_B_THRESHOLD = 7  # >=7 = warm (direct), <7 = cold (educational)
```

**2. Vertical send-schedule:**
```python
LOOPS_SCHEDULES = {
    "med_spa":      ("WED", 19, 0),   # Wed 7:00 PM CST
    "pi_law":       ("WED", 19, 0),
    "real_estate":  ("TUE", 8, 0),
    "home_builder": ("MON", 6, 30),
}
```

**3. Output discipline — replace heartbeat with structured summary:**
```python
def brenda_run():
    tallies = {"scored": 0, "track_a": 0, "track_b": 0, "enrolled": 0,
               "hot_leads_flagged": 0, "errors": 0, "skipped_unscored": 0}
    ...
    summary = (
        f"BRENDA RUN — {now_iso}\n"
        f"  Scored: {tallies['scored']} contacts\n"
        f"  Track A (cold, <{TRACK_B_THRESHOLD}): {tallies['track_a']}\n"
        f"  Track B (warm, >={TRACK_B_THRESHOLD}): {tallies['track_b']}\n"
        f"  Loops sequences enrolled: {tallies['enrolled']}\n"
        f"  Hot leads → Marcus: {tallies['hot_leads_flagged']}\n"
        f"  Errors: {tallies['errors']}\n"
        f"  Per-contact details: ..."
    )
    return summary
```

The post-run hook will receive this string (length >>200 chars), persist it as `raw_output`, and CRO sweeps + morning briefing now see real work.

**4. Iron rules enforcement:**
- Pricing-mention check — regex over composed copy before send, block + log on hit
- "Written to OWNER" check — already enforced at contact-level
- Hot-lead Twilio alert path — confirm wired or add
- Secrets via env — already correct in current code

**5. Update agent prompt file** to say "this prompt is documentation of intent; enforcement lives in workflow.py" so future editors don't re-fall-in-the-trap.

## Verification per sub-phase

Per verification-before-completion standing practice:

- **R1 Brenda**: run live in staging-equivalent (or with `BRENDA_DRY_RUN=1` env), inspect `raw_output` is >=200 chars, contains tally fields, scoring applied per new rubric. Live verify against 3 hand-picked contacts known to fall in 380/DFW/TX/national.
- **R2 Randy**: re-enroll the 29 stuck contacts. Verify in GHL UI count moves to 0 (or close).
- **R3 Darrell**: same shape as R1.
- **R4 Marcus**: query last 10 marcus_logs for territory breakdown; if national-skewed without KEYAPI being to blame, code fix; if KEYAPI-driven, ticket KEYAPI not Marcus.

## Risk callouts

1. **Re-enrollment of Randy's stuck 29** — may trigger sequences for contacts already past the window. Verify each before pushing.
2. **Scoring rubric change is behavioral** — contacts scored under old rubric may rescore differently. ONE-TIME backfill optional but recommend NOT (avoid double-emailing).
3. **Heartbeat → real-summary breaks log parsers** — if any downstream relies on the 55-char shape, it'll break. Survey before R1 ships.
4. **Coordination with the CRO PAUSE directive** — "ALL email campaigns PAUSED until ~Jul 6 warmup." Workflow rewrites can ship + verify (build, lint, dry-run) but actual sends stay gated by the existing pause flag. Confirm the rewrites respect that flag.

## Open questions for Michael

1. Greenlight Option B (translate to Python)?
2. Brenda first or Randy first? CRO suggested order: Brenda → Randy → Darrell. Randy has the largest known leak (29 stuck), so Randy first is also defensible.
3. The pause directive — confirm rewrites can ship + verify with sends gated, OR fully pause this work until ~Jul 6?
4. Marcus discovery scope — go now, or wait for Randy/Brenda fix and verify Marcus's behavior post-fix?

## What ships in this PR

Just this plan. Zero code.

After greenlight, R1 (Brenda) ships as PR — workflow.py rewrite + agent prompt header note + verification log in PR description.

## Rollback

Plan-only. Revert = revert. Per-sub-phase rollback is a single git revert (workflow.py is the only file changed per phase, plus the agent prompt note).
