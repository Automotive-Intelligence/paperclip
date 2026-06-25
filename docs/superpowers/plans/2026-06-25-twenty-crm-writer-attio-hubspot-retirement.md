# Twenty CRM Writer + Attio/HubSpot Retirement

**Initiative owner:** Build & Tech
**Plan author:** Infrastructure (now merged into B&T per 2026-06-23 scope merge)
**Date:** 2026-06-25
**Status:** DRAFT — awaiting Michael greenlight before Phase 1 ship
**Unlocks:** [[project_crm_consolidation_to_twenty]] + [[project_hubspot_retirement]] + Attio retirement (per [[feedback_attio_retired_cleanup]])

## Goal

Build a `tools/twenty.py` writer client and route all CD + AutoIntel prospect writes through it, replacing the two legacy CRM integrations (Attio, HubSpot) that are still live in production despite the org decision to consolidate on Twenty.

## Why now (the situation we surfaced 2026-06-24)

Infrastructure investigation found that the "Attio retirement" and "HubSpot retirement" memory commitments were never actually shipped — they were declared done but the code never moved:

- `config/runtime.py:188-192` default `business_crm_map = {"aiphoneguy": "ghl", "callingdigital": "attio", "autointelligence": "hubspot"}` — and `BUSINESS_CRM_MAP` env-var is unset in Doppler, so the default IS the live config.
- `tools/crm_router.py` only knows `ghl` / `hubspot` / `attio` branches. There is no `twenty` branch.
- `tools/twenty.py` does not exist.
- `ATTIO_API_KEY` + `HUBSPOT_ACCESS_TOKEN` are still set on Railway.
- Both old CRMs are still being billed; agents are still writing leads to them (per recent `agent_logs.crm_provider` values).

Net: the "we retired Attio/HubSpot" claim is aspirational. Until a Twenty writer exists, there is nowhere to route the writes to — and any teardown would lose lead data.

## What we are NOT doing

- Not building a Twenty reader / sync-back surface. This is write-only, mirroring the existing `push_prospects_to_*` contract.
- Not migrating historical Attio/HubSpot records into Twenty. (Twenty's `crm.worshipdigital.co` workspace was bootstrapped fresh per [[reference_avi_twenty_bootstrap]]; backfill is a separate initiative if Michael wants it.)
- Not building a Twenty MCP server. The contract is a single Python function `push_prospects_to_twenty()` called by `crm_router`.
- Not touching GHL routing — AiPhoneGuy still routes to GHL per the default map.

## Live state inventory (verified 2026-06-25)

| Surface | State |
|---|---|
| `crm.worshipdigital.co` (Twenty WD workspace) | HTTP 200, GraphQL endpoint live, REST endpoint live |
| `bookd.twenty.com` (Twenty Book'd workspace) | HTTP 200 |
| Twenty AvI workspace | Bootstrapped per [[reference_avi_twenty_bootstrap]] — URL TBD-in-memory |
| `TWENTY_*` Doppler secrets | **NONE SET** — Michael-hands gate before Phase 1 ships |
| `tools/twenty.py` | Does not exist |
| `tools/attio.py` refs (other modules) | crm_router (2), runtime config (2), brenda role string (3), marcus (1), 8 scripts (mostly one-off historical) |
| `tools/hubspot.py` refs (other modules) | crm_router (2), runtime config (3), app.py (~27 — needs audit), `rivers/automotive_intelligence/{deals,cleanup,workflow}.py` (~22 — needs audit) |

## Phases

Each phase is one PR. Verification gate between phases. No phase 2 ships until phase 1 is verified green in production.

### Phase 1 — Twenty writer client (1 PR, ~1 day)

Build `tools/twenty.py` mirroring the `push_prospects_to_hubspot` contract:

```python
def push_prospects_to_twenty(
    prospects: list,
    source_agent: str = "marcus",
    business_key: str = "callingdigital",
) -> list
```

**Required behavior parity with HubSpot writer:**
- Idempotent on email — search-then-create pattern (mirror `_search_contact_by_email`).
- Per-business workspace routing — `callingdigital` writes to WD workspace, `autointelligence` writes to AvI workspace, `bookd` writes to Book'd workspace. Workspace URL + API key resolved from per-business env vars (`TWENTY_WD_URL` + `TWENTY_WD_API_KEY`, etc.) — NOT a single shared key, since Twenty workspaces are per-tenant.
- Returns same result-dict shape as the HubSpot writer (`business_name`, `contact_id`, `status`, error fields) so `log_crm_push` keeps working without changes.
- Raises `ValueError("Twenty credentials not configured...")` if the workspace key for the requested business is missing. Same pattern as HubSpot writer.
- Uses Twenty REST API (`POST /rest/people`, `POST /rest/companies`) — REST is simpler than GraphQL for write-only and matches our existing pattern.

**Twenty API authentication:** Bearer token in `Authorization` header, per Twenty docs. One API key per workspace (generated from each workspace's Settings → Developers → API Keys).

**`twenty_ready(business_key)` helper:** sibling to `hubspot_ready()` / `attio_ready()`, takes business_key arg since readiness is per-workspace not global.

**Tests:** unit tests in `tests/twenty_client_checks.py` mirroring `tests/hubspot_client_checks.py` structure. Mock the requests layer; verify search-then-create, error mapping, business-key routing.

**Verification gate before Phase 2:**
- All unit tests green
- Manual smoke: `python -m tools.twenty push_prospects_to_twenty` with 1 dummy prospect → confirm record appears in `crm.worshipdigital.co` UI
- `tools/twenty.py` does NOT yet get called from `crm_router` — it's importable but unused. Zero production impact.

### Phase 2 — Wire Twenty into the router (1 PR, ~half day)

- Add `twenty` branch to `tools/crm_router.py:push_prospects_to_crm()` (mirror existing hubspot/attio elif structure).
- Add `twenty` to `crm_status_snapshot()` provider_readiness dict.
- Add `twenty_api_key_present` (or per-workspace dict) to `RuntimeSettings` in `config/runtime.py`.
- Add `twenty_ready(business_key)` method on `RuntimeSettings`.
- Add `twenty` arm to `crm_provider_ready()`.
- Update startup-warnings loop to handle `twenty`.

**Default `business_crm_map` is NOT changed in this phase.** Live routing still goes to Attio/HubSpot. Twenty is now wired but unused. Zero production impact again.

**Verification gate before Phase 3:**
- Tests in `tests/crm_routing_checks.py` updated and green
- `python -c "from tools.crm_router import crm_status_snapshot; print(crm_status_snapshot())"` shows twenty in readiness dict
- Production routing still flows to Attio (CD) + HubSpot (AvI) — nothing changed for live traffic

### Phase 3 — Michael sets Twenty API keys (no code, Michael-hands gate)

I cannot create Twenty API keys autonomously — they're generated through each Twenty workspace UI by an authenticated user. This is a hard gate.

**Paste-ready Michael task:**

> For each Twenty workspace (WD, AvI, Book'd), go to Settings → Developers → API Keys → Create. Generate a key per workspace, then in terminal:
> ```
> doppler secrets set --silent TWENTY_WD_URL=https://crm.worshipdigital.co TWENTY_WD_API_KEY=<paste>
> doppler secrets set --silent TWENTY_AVI_URL=<avi workspace url> TWENTY_AVI_API_KEY=<paste>
> doppler secrets set --silent TWENTY_BOOKD_URL=https://bookd.twenty.com TWENTY_BOOKD_API_KEY=<paste>
> ```
> Railway picks them up via the Doppler sync — no manual paste.

**Verification gate before Phase 4:**
- All three TWENTY_*_API_KEY entries set in Doppler
- Phase 2 code shows `twenty_ready("callingdigital") == True`, etc. via Railway logs

### Phase 4 — Flip default `business_crm_map` to twenty (1 PR, the actual cutover)

Change `config/runtime.py:188-192`:
```python
default_business_crm_map = {
    "aiphoneguy": "ghl",
    "callingdigital": "twenty",      # was "attio"
    "autointelligence": "twenty",    # was "hubspot"
    "bookd": "twenty",               # new — Book'd was previously unmapped
}
```

**This is the live-traffic flip.** First lead written after deploy lands in Twenty, not Attio/HubSpot.

**Risk callouts:**
- If Phase 1 unit tests missed an edge case (e.g. duplicate-email handling), it surfaces in production. Mitigation: deploy Phase 4 during low-traffic window (after 8 PM CDT), monitor `agent_logs.crm_provider="twenty"` for the first 10 writes, rollback by reverting the runtime.py default if any fail.
- Attio/HubSpot wires stay live but unused (router won't pick them) — so a rollback is a single-line revert + redeploy, not a panic.

**Verification gate before Phase 5:**
- 24 hours of clean production writes to Twenty for both CD + AvI agents
- Zero `crm_push_logs` entries with `crm_provider="twenty"` and error status
- Manual UI check of Twenty WD + Twenty AvI workspaces showing new contacts arriving

### Phase 5 — Dead code + dead env teardown (1 PR, ~half day)

ONLY after Phase 4's 24-hour verification window is clean:

- Delete `tools/attio.py`
- Delete `tools/hubspot.py`
- Remove `attio` / `hubspot` branches from `tools/crm_router.py`
- Remove `attio_api_key_present` / `hubspot_api_key_present` / `attio_ready` / `hubspot_ready` from `config/runtime.py`
- Update brenda role strings (`agents/callingdigital/brenda.py` lines 13/15/22) from "Attio Workflow Architect" to "Twenty Workflow Architect"
- Update `services/cockpit_bridge.py:179` brenda role string
- Update `tests/crm_routing_checks.py` — remove Attio/HubSpot test cases
- Audit + clean the 8 one-off scripts (enrich_verified, salvage_marcus_scrapes, etc.) — likely delete since they were one-off historical
- `doppler secrets delete ATTIO_API_KEY HUBSPOT_API_KEY HUBSPOT_ACCESS_TOKEN`
- Cancel Attio + HubSpot subscriptions through their billing UIs (Michael-hands)

**Verification gate before close:**
- `grep -rn "attio\|hubspot" --include="*.py"` returns 0 hits
- Sweep next morning shows clean env-var snapshot (no orphan TWENTY_* drift)
- Telemetry flag closed on `infrastructure_state.md`

## Cost recovery

Per [[reference_crms]]: Attio + HubSpot are both paid subscriptions. Phase 5 unlocks cancellation of both — net subscription savings TBD pending Michael confirming current monthly rates.

## Rollback strategy per phase

| Phase | Rollback |
|---|---|
| 1 | Delete `tools/twenty.py` — zero impact, never called |
| 2 | Revert PR — router goes back to 3-branch state, zero impact (no production traffic was on twenty branch) |
| 3 | N/A — secrets-only |
| 4 | Single-line revert of `config/runtime.py` default map, redeploy — lead writes resume to Attio/HubSpot within one deploy cycle |
| 5 | Real teardown — rollback means re-creating deleted files from git history + re-subscribing to vendors. Only attempted if Phase 4 was clean for 24h+ |

## Open questions for Michael

1. **AvI Twenty workspace URL** — needs confirming. `reference_avi_twenty_bootstrap` says bootstrapped but doesn't memo the live URL. Phase 3 gate.
2. **Per-business write contract** — should `bookd` writes route to Twenty Book'd workspace? Currently Book'd doesn't have an entry in default_business_crm_map at all. Recommendation: add `"bookd": "twenty"` in Phase 4 since the workspace exists.
3. **Backfill** — do we want historical Attio/HubSpot records migrated into Twenty before cancelling those subscriptions? If yes, that's a separate initiative AFTER Phase 5 with its own plan.

## Out-of-scope but adjacent

- Twenty MCP server for Paperclip agents to query CRM (read surface) — separate initiative.
- Loops-Twenty bi-directional sync for the WD email program — separate initiative.
- AIPG entity / GHL migration — out of scope; AIPG stays on GHL per memory.

## What ships when

- Phase 1 PR: shippable immediately on greenlight (~1 day to code + test)
- Phase 2 PR: shippable immediately after Phase 1 verification (~half day)
- Phase 3: blocks on Michael generating Twenty API keys
- Phase 4 PR: shippable after Phase 3 verification (low-traffic deploy window)
- Phase 5 PR: shippable 24h after Phase 4 verification

Total wall-clock: ~3-5 days if Michael's API-key gate clears quickly. Two days of those are verification windows, not code time.
