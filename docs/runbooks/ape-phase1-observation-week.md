# APE Phase 1 — Observation Week Runbook

## Goal
Validate that Infrastructure APE behaves safely + usefully across a 7-day window before Phase 2 (Build & Tech) is enabled.

## Day 0 — Enable
Set on Railway / Doppler:
- `PERSONA_EXECUTOR_INFRASTRUCTURE=on`

Verify autopilot picks up next Infrastructure flag within 60s.

## Daily checks (every morning after the 7:30 sweep + 8 AM brief)
1. Read the brief — does the `Infrastructure` row look right?
2. Check inbox for any `[Infrastructure] Auto-shipped:` emails. Any caution banners? Any questions for you?
3. Read the 6 PM digest from yesterday — does the action list make sense?
4. Spot-check 1 ship by clicking the Ship ID and reading the audit envelope in Postgres.

## Watchlist for the week
- Reviewer rejection rate. Goal: 5–15% range. Pull via:
  ```sql
  SELECT verdict, COUNT(*) FROM reviewer_transcripts
  WHERE created_at >= NOW() - INTERVAL '7 days'
  GROUP BY verdict;
  ```
- Reply telemetry. Goal: zero REVERTs, zero PAUSEs.
- `autonomous_ship_health` findings. Goal: zero.

## Graduation gate to Phase 2
ALL of:
1. >=5 ships across >=3 days
2. Zero REVERT replies
3. Reviewer rejection rate within 5–15%
4. At least one digest fired and read sensibly
5. At least one caution banner fired AND was warranted
6. CTO morning sweep shows no `autonomous_ship_health` warnings tied to APE ships
7. No `PAUSE` replies sent

If any fail: tune (prompt, tools, thresholds) and reset the 7-day window.

## Kill switch
- Single ship: reply REVERT
- Persona 24h: reply PAUSE
- Global 24h: reply PAUSE ALL
- Hard kill: `PERSONA_EXECUTOR_ENABLED=off` on Railway/Doppler
- Nuclear: `railway service stop persona-executor`
