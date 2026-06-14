# Autonomous Persona Executor (APE) — Design Spec

**Date:** 2026-06-14
**Author:** Infrastructure persona (Claude Opus 4.7) via brainstorming with Michael Rodriguez
**Status:** Design approved, ready for implementation planning
**Phase 1 target:** Infrastructure persona auto-execution (7-day proof window before Phase 2)

---

## Context & problem

AVO's 9 persona chats post `🏁` flags to each other via the avo-telemetry markdown protocol. Today, when a flag lands in a target persona's "Flags for other chats" section, it sits there until Michael personally opens that chat. The cockpit-bridge polls these files and writes them to a Postgres `agent_handoffs` table, but no service picks up flags targeting CHAT personas and executes them autonomously — only flags targeted at agent personas (axiom/clint/wade) get downstream execution today.

This violates the AVO autonomous-revenue thesis (`project_avo_vision.md`): Michael's presence shouldn't be load-bearing for revenue motion. Cross-chat flags becoming stale because Michael hasn't opened a chat = bottleneck.

**Goal:** Add autonomous execution for flags targeting persona chats. Each chat gets an AI executor that picks up its own flags, plans, executes, verifies, and ships. Michael is notified post-ship via Resend email and retains kill-switch + revert authority. The work happens; Michael stays in the loop as a judge of outcomes, not as a code reviewer.

**Critical design constraint (locked during brainstorm):** Michael is honest that he does not function as a code-review safety net today — he rubber-stamps PRs. The design treats AI verification (executor + adversarial reviewer) as the actual safety net, with the morning brief + revert kill-switch as the catch-net for failures.

---

## Decisions locked during brainstorm

### Decision 1: Full autonomous ship (vs draft-and-wait)
Each persona AI completes the work and ships it autonomously. Michael receives a post-hoc audit email per ship (high-impact tier) or per persona per day (routine tier).

### Decision 2: Tiered email cadence
- **High-impact ships:** immediate Resend email per ship
- **Routine ships:** consolidated 6 PM CDT daily digest per persona
- Classification rule is objective, applied identically across personas via the persona system prompt

### Decision 3: Scope sequence
Phase 1: Infrastructure → Phase 2: Build & Tech → Phase 3+: remaining 7 personas, one-at-a-time with observation week each. Per-persona feature flag in Railway env (`PERSONA_EXECUTOR_<PERSONA>=on|off`).

### Decision 4: Code auto-merge for Build & Tech (Phase 2+)
Auto-merge to `main` allowed without human PR review, because Michael's review today is rubber-stamp theater. AI verification-before-completion is required and uncompromised — auto-merge removes human review only, not verification. Reversibility via `git revert` is the kill-switch.

### Decision 5: Safety architecture (under honest framing)
- AI multi-checkpoint: executor runs `verification-before-completion`; adversarial reviewer (separate Claude session, distinct prompt) reviews diff before merge
- Plain-language audit email: every high-impact ship includes WHAT/WHY/EVIDENCE/UNDO/RISK in English Michael can judge without reading code
- Observable outcomes loop: morning brief CTO row surfaces `autonomous_ship_health` regressions; Michael judges from dashboard not code

---

## Architecture

### Core loop

```
Flag posted in <persona>_state.md
        ↓
cockpit-bridge polls every 60s, parses 🏁 flag                  (existing)
        ↓
Writes to agent_handoffs Postgres                                (existing)
        ↓
persona_executor service polls agent_handoffs every 60s          (NEW)
        ↓
For each flag whose target persona has APE enabled:
  ├── Spawn ephemeral Claude session via Anthropic SDK
  │     with persona system prompt + flag content + memory context
  ├── Session reads flag, plans (writing-plans skill),
  │     executes per tool allowlist, verifies (verification-before-completion)
  ├── Session classifies action: GREEN/AMBER/RED + ROUTINE/HIGH-IMPACT
  ├── Adversarial reviewer session reviews the audit envelope
  │     (max 3 revise cycles; HALT posts flag back to Infrastructure)
  ├── If approved: commit + push + (Build & Tech only) auto-merge
  ├── HIGH-IMPACT → fires immediate Resend email
  ├── ROUTINE → appends to today's persona-digest queue
  └── On success: marks agent_handoffs row complete + runs close_flag.sh
        ↓
6 PM CDT daily cron: per-persona digest emails go out
```

### Concurrency model
- Sequential per persona (max 1 in-flight session per persona at any time)
- Cross-persona parallelism allowed
- 10-minute hard timeout per flag; on timeout, flag posts back to Infrastructure as "couldn't execute"

### Service surface (new)
- `services/persona_executor.py` — main poller + dispatcher
- `services/persona_prompts/infrastructure.md` — Infrastructure persona system prompt
- `services/persona_prompts/infrastructure_tools.md` — explicit tool allowlist
- `services/persona_prompts/build_and_tech.md` — Phase 2
- `services/persona_prompts/build_and_tech_tools.md` — Phase 2
- `services/adversarial_reviewer.py` — reviewer Claude session wrapper
- `services/ape_resend.py` — high-impact email + digest builder + reply parser
- `services/ape_ship_telemetry.py` — pre/post metric correlation

---

## Action classification

### Impact tier (drives email cadence)

A ship is **HIGH-IMPACT** if ANY of:
1. Touches external systems (CRMs, payment, email/SMS providers, social platforms, ad networks)
2. Modifies secrets / env vars / vendor configurations
3. Affects legal entities, domains, DNS, billing relationships
4. Communicates externally (client emails, public posts, brand-site customer copy)
5. Spends money or commits to future payment

Otherwise **ROUTINE** (internal-only state changes).

### Reversibility class (drives auto-ship eligibility)

- **🟢 GREEN — fully reversible.** Single command undoes. Auto-ship allowed.
  Examples: git work, memory edits, telemetry updates, sweep config tweaks.
- **🟡 AMBER — reversible with effort.** Some manual step required. Auto-ship + immediate email + caution banner.
  Examples: env var changes, Doppler rotations, archived Vercel projects.
- **🔴 RED — irreversible.** Once shipped, can't be undone. **NEVER auto-ships in v1.** Always halts and posts a "needs Michael's ACK" flag back.
  Examples: sent client emails, published social posts, money committed, vendor contracts, DNS propagation.

The reviewer enforces the RED gate hardest: if executor proposes a RED action, reviewer halts regardless of other signals.

---

## Audit email design

### High-impact ship — immediate

**Subject:** `🚨 [<Persona>] Auto-shipped: <one-line summary>`

**Body sections (in order):**
1. **Optional caution banner** at top (triggers below)
2. **WHAT was done** — one English sentence
3. **WHY** — flag ID + posted timestamp + flag content excerpt
4. **EVIDENCE it works** — actual smoke-test output in plain text
5. **RISK profile** — AI's GREEN/AMBER/RED classification with reasoning
6. **UNDO** — copy-pasteable revert command + "reply REVERT" shortcut
7. **ADVERSARIAL REVIEWER said** — APPROVE / APPROVE-with-notes / (HALT cases don't reach this email since they didn't ship)
8. **Question for you** (optional, when AI has a strategic question)
9. **Footer** (every email — see below)

### Caution banner triggers
Any of:
- First-time action (APE has never done this exact action type before)
- Reviewer raised concerns but didn't block (APPROVE-with-notes)
- Action touched a known risk surface (vendor with recent outage, file path in 3+ incident memories)
- AI explicit uncertainty signal in audit envelope
- AI has a strategic question for Michael

Banner reads:
```
⚠️ HEY, YOU SHOULD ACTUALLY LOOK AT THIS, MICHAEL.
Reason: <one-sentence honest explanation>
```

### Footer (every email)
```
HOW TO INTERACT WITH THIS SHIP
Reply with one of:
  REVERT       — undo this ship
  PAUSE        — disable <Persona> autopilot for 24h
  PAUSE ALL    — disable ALL persona autopilots for 24h
  ASK <text>   — send <text> as a question back to the AI
  NOTES <text> — log a note against this ship (no action)

WHERE TO ADJUST AUTOPILOT BEHAVIOR
  Persona prompt: paperclip/services/persona_prompts/<persona>.md
  Tool allowlist: paperclip/services/persona_prompts/<persona>_tools.md
  Per-persona switch (Railway env): PERSONA_EXECUTOR_<PERSONA>=on|off
  Global kill (Railway env): PERSONA_EXECUTOR_ENABLED=off

NOT SURE WHAT TO DO?
  Reply HELP and the AI will explain the choices in plain English.
```

### Routine ship — daily digest
- **Subject:** `🛠 [<Persona>] Today's autopilot — N routine ships`
- **Time:** 6 PM CDT daily cron, per-persona
- **Body:** list of each ship in 1-3 lines (what / why / undo hash)
- **No-op suppression:** if persona had zero ships, no email

### Reply parser (Resend inbound webhook → Paperclip)
- Each high-impact email carries unique ship-ID in headers + body
- Reply with `REVERT` / `PAUSE` / `PAUSE ALL` / `ASK <text>` / `NOTES <text>` / `HELP` triggers Paperclip endpoint
- Endpoint looks up ship-ID, executes stored undo command OR writes pause flag OR queues ASK as a flag back to executor
- Confirmation email back

---

## Kill-switch hierarchy

| Layer | Trigger | What happens |
|---|---|---|
| 1. Per-ship REVERT | Reply "REVERT" to email | Runs stored undo, sends confirmation |
| 2. Per-persona 24h PAUSE | Reply "PAUSE" or "PAUSE Build & Tech" | Persona skipped 24h, auto-resumes |
| 3. Global 24h PAUSE ALL | Reply "PAUSE ALL" or voice flag "halt all autopilots" | All personas skipped 24h |
| 4. Hard kill via env | Set `PERSONA_EXECUTOR_ENABLED=off` in Doppler/Railway | Service polls env every 30s, flips immediately |
| 5. Nuclear | `railway service stop persona-executor` | Service down |

Layers 1–3 work from phone via email reply. Layer 4 takes ~30 seconds via Doppler. Layer 5 takes 90 seconds via Railway.

---

## Detection — how APE catches its own misbehavior

Four signal streams feed the morning brief CTO row:

1. **`autonomous_ship_health`** (new sweep check) — for each ship in last 24h, correlates pre/post metrics. Lead intake dropped after a Twenty config tweak? Flag. Outbound stopped sending after an Instantly campaign edit? Flag.
2. **`reviewer_rejection_rate`** (new) — if a persona's reviewer rejects >30% of executor proposals, the prompt or tools are misaligned.
3. **`existing_error_patterns`** (already wired) — agent_logs errors spike after a ship → likely correlation.
4. **`reply_telemetry`** (new) — if REVERT or PAUSE replies fire >1x/week, autopilot is mis-calibrated. Surface in weekly Pit Wall digest.

---

## Audit + forensics — where data lives

| Layer | Data | Lifetime |
|---|---|---|
| Postgres `agent_handoffs` | Execution record per flag (flag content, outcome, ship hash, undo command, reviewer verdict) | Permanent |
| Postgres `autonomous_ship_telemetry` (new) | Pre/post metrics per ship | 90d rolling |
| GitHub commit history | All code/memory/telemetry ships | Permanent |
| Resend log | Every email sent + reply received | 90d (Resend default) |
| Doppler audit log | All secret operations | Per Doppler tier |
| Reviewer transcripts (new Postgres) | Adversarial reviewer reasoning per ship | 90d |

---

## Phase 1 scope — Infrastructure persona only

### v1 actions Infrastructure APE can ship

**🟢 GREEN (routine → digest):**
1. Refresh stale memory files when flag posted
2. Update `infrastructure_state.md` when sweep findings warrant
3. Post flags to other personas when surveillance surfaces issues they own
4. Close own flags when downstream personas mark resolved via `close_flag.sh`
5. Add new domains to `MONITORED_DOMAINS` when a brand site goes live
6. Adjust sweep thresholds when false-positive patterns observed
7. Expand `PLATFORM_ENV_PREFIXES` filter when noise detected

**🟡 AMBER (auto-ship + immediate email + caution banner):**
8. Rotate API tokens via Doppler when age >30d (prior version preserved for one-step revert)
9. Update Railway env vars for non-secret config (`BRIEFING_RECIPIENT`, sweep thresholds)
10. Archive a Vercel project flagged dormant >180d (one-step undo via Vercel API)

**🔴 RED (NEVER auto-ships — flag back to Michael):**
- External comms (client emails, social posts)
- Money / vendor commitment
- DNS / domain
- Secret exfiltration
- Legal entity changes

### Tool surface — Infrastructure persona

**Allowed:**
- Read/write `~/avo-telemetry/*.md` + git commit/push to main
- Read paperclip code (for context; NOT write)
- Read Vercel/Railway/Doppler/GitHub APIs
- Write Doppler (AMBER rotation only)
- Read agent_logs Postgres
- Send Resend email (own ship notifications)
- Run `~/avo-telemetry/scripts/close_flag.sh`

**Denylisted:**
- Write to paperclip directory (Phase 2 / Build & Tech)
- Write to brand-site repos (future per-brand personas)
- External API writes to CRMs / payment / social / ad networks (always RED)
- DNS / registrar APIs
- Client comm channels (Gmail send-as, Slack, iMessage proxies)

### First proof-of-loop test (Day 1)
A fabricated test flag from Infrastructure to itself:
```
🏁 FLAG FOR: Infrastructure
**What:** Test ship — append a single comment line
"# APE proof-of-loop 2026-06-14" to the top of infrastructure_state.md scope reference section.
**Why now:** Validates full lifecycle without touching anything that matters.
**By when:** today
```
APE picks it up, executes the trivial edit, sends the digest email (routine), closes the flag.
Confirms spawn → execute → verify → reviewer → close → email pipeline is whole.

### Phase 1 → Phase 2 graduation criteria
Phase 2 (Build & Tech APE) goes live when ALL true:
1. Infrastructure APE shipped ≥5 flags successfully across ≥3 days
2. Zero REVERT replies in that window
3. Reviewer rejection rate stable in 5–15% range
4. The 6 PM digest fired at least once and reads sensibly
5. At least one caution banner fired AND was warranted
6. CTO morning sweep shows no `autonomous_ship_health` warnings tied to APE ships
7. Michael hasn't replied PAUSE for any reason

If any fail, tune until pass.

---

## Implementation phases (estimated 5–7 days for Phase 1)

| Day | Deliverable | PR title prefix |
|---|---|---|
| 1–2 | `persona_executor` service skeleton + Infrastructure persona prompt + tool allowlist | `[INFRA-APE-1]` |
| 3 | Adversarial reviewer prompt + 3-cycle revise loop | `[INFRA-APE-2]` |
| 4 | Resend integration (high-impact + digest), caution banner, footer | `[INFRA-APE-3]` |
| 5 | Reply parser (Resend inbound webhook → REVERT/PAUSE/ASK actions) | `[INFRA-APE-4]` |
| 6 | `autonomous_ship_health` sweep check + `autonomous_ship_telemetry` Postgres table | `[INFRA-APE-5]` |
| 7 | First proof-of-loop test + 7-day observation begins | `[INFRA-APE-6]` |

Each day = one PR. Each PR opens with `superpowers:writing-plans`. Each ship gates through `superpowers:verification-before-completion`. Per `feedback_infrastructure_superpowers_standing_practice` memory.

---

## Out of scope (explicit non-goals for v1)

- **Build & Tech autopilot** — Phase 2, only after Phase 1 7-day clean window
- **Pit Wall / Internal Marketing / Revenue & Sales / B2B Ops / Agent Empire / Sales Marketing / Client Marketing autopilots** — Phase 3+, one persona at a time
- **Auto-execution of RED-class actions** — v2 candidate; would introduce ACK-once-and-fire-later flow
- **Cross-persona orchestration** (e.g., Infrastructure → Build & Tech handoffs auto-executing) — v2 candidate
- **Learning from prior ship outcomes** (stateless v1; v2 might add a "recent ship outcomes" memory the executor reads)
- **Voice-cockpit kill commands** beyond what cockpit-bridge already routes — v2 candidate (current voice path: post a flag to Infrastructure that says "PAUSE ALL")

---

## Open risks (flagged for observation, not blockers)

1. **Adversarial reviewer convergence** — reviewer is a separate Claude session with distinct prompt but same model family. Could share blind spots with executor. Mitigation: distinct prompting, observe reject rate, tune.
2. **Stateless = no learning across flags** — each flag fresh; same mistake twice won't self-correct unless morning brief catches it. v2 candidate.
3. **Tool surface allowlist drift** — too restrictive halts legitimate work; too permissive ships risky action. Will need empirical adjustment first week.
4. **Persona prompt bugs cascade** — a bug in the system prompt affects every subsequent ship until fixed. Mitigation: standard PR review for prompt files even with code auto-merge on (since prompts shape AI behavior beyond just code semantics).
5. **Cost** — every flag spawns a fresh Claude session. If flag volume grows large, costs grow. Mitigation: per-persona switch lets you disable expensive personas; daily digest surfaces ship volume.

---

## Related memory files

- [[project_avo_vision]] — autonomous-revenue thesis this design implements
- [[project_infrastructure_chat_scope]] — Infrastructure persona scope
- [[feedback_infrastructure_superpowers_standing_practice]] — `writing-plans` + `verification-before-completion` discipline
- [[feedback_close_flag_protocol]] — close_flag.sh that APE will run
- [[project_secrets_management]] — Doppler is SoT; APE rotates via Doppler API
- [[feedback_pit_leader_posture]] — execute-don't-relitigate discipline applied to APE persona prompts

---

## Approval

Design walked through 6 sections during 2026-06-13/14 brainstorm. Each section ACK'd by Michael:
- Section 1: Architecture overview — APPROVED
- Section 2: High-impact classification + auto-merge nuance — APPROVED
- Section 2.5: Safety architecture under honest framing — APPROVED
- Section 3: Executor lifecycle + persona system prompt — APPROVED (with Michael's honest acknowledgment that he can't evaluate the lifecycle directly; accountability stays with executor + observability surface)
- Section 4: Email flow + caution banner + footer — APPROVED
- Section 5: Kill-switch + reversibility classification — APPROVED
- Section 6: Phase 1 scope — APPROVED (pending this document review)
