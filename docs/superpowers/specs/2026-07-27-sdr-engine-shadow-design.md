# Design Spec: The SDR Engine (shadow mode)

**Date:** 2026-07-27
**Author:** The Sales Desk (AVO), driving; Michael Rodriguez, owner
**Status:** design direction set (Michael delegated the calls), pending spec sign-off before writing-plans
**Sub-project of:** the autonomous SDR desk (#2 of 4)
**Governed by:** `docs/superpowers/specs/sdr-desk-principles.md` (BINDING). Consumes sub-project #1 (`services/sdr_verification_gate`).

---

## 1. Why this exists

Sub-project #1 built the Verification Gate: given a candidate, it verifies real primary site / real defect / real contact and emits a verdict that drives `approval_queue`. But nothing calls it yet. This sub-project makes the desk **self-producing**: a Railway engine that pulls fresh signals, runs each candidate through the gate, and routes verified opportunities to the right CRM, 24/7, no operator. It ships in **shadow mode** (produces a digest, writes/sends nothing live) so the verify-rate is proven before any autonomous write. This is Michael's core ask: "SDR agents that create our opportunities off signals," running hands-free.

## 2. Scope of THIS spec

Build one bounded Railway engine, `services/sdr_engine.py`, plus the `business_key` normalization it requires. ONE signal source (`permit_feed`, WD rebuild motion). Shadow mode default. No live send, no research/voice skills, no multi-source — those are later sub-projects.

## 3. What already exists (reused, not rebuilt)

- `services/sdr_verification_gate.run(VerificationRequest) -> dict` — the gate (#1).
- `tools/permit_feed` — the signal source (WD rebuild permits).
- `tools/crm_router.push_prospects_to_crm(prospects, source_agent, business_key)` — per-brand CRM write.
- `services/approval_queue` — the risk gate (auto / pending / drop).
- `services/studio_social_engine` — the Railway engine PATTERN to mirror: dry-run default, produce -> gate -> receipt, `POST /admin/run-*` on-demand entry, schedule once proven.
- `config/runtime.py::business_crm_map` — canonical keys `aiphoneguy`/`callingdigital`/`autointelligence`/`bookd`; per-brand Twenty readiness flags (`twenty_wd_ready`, `twenty_avi_ready`, ...).

## 4. The business_key normalization (the parked prereq #1 flagged)

The desk speaks `wd`/`avi`/`aipg`/`bookd`; the runtime speaks `aiphoneguy`/`callingdigital`/`autointelligence`/`bookd`. Left unclosed, the first real WD/AvI prospect misroutes to the GHL default. Fix: one canonical mapping, applied at the engine boundary before any `crm_router` call:

| Desk key | Runtime key | CRM |
|---|---|---|
| `aipg` | `aiphoneguy` | GHL |
| `wd` | `callingdigital` (WD's Twenty; confirm the exact runtime key wires to `twenty_wd_ready`) | Twenty (WD) |
| `avi` | `autointelligence` | Twenty (AvI) |
| `bookd` | `bookd` | Twenty (Book'd) |

The engine resolves the desk key to the runtime key and asserts the target CRM is `*_ready` before writing; if not ready, the opportunity holds in `approval_queue` pending rather than misrouting.

**RESOLVED 2026-07-27 (was open item 11.1):** `wd` -> `callingdigital` is confirmed from source (`services/intent_inbound.py:155` `"wd": "callingdigital"  # WD workspace`; `tools/twenty.py` maps `callingdigital` -> `TWENTY_WD_URL`/`TWENTY_WD_API_KEY`/`crm.worshipdigital.co`). This normalization is a **pure boundary translation** (desk keys -> the runtime keys that already work); it does NOT rename the underlying `callingdigital` slug. The full CD->WD internal-slug rename (125 refs / 34 files / 2 dirs / live-data key migration) is a DEFERRED, separate low-priority project — no customer sees the slug, secrets are already `TWENTY_WD_*`, and a hard rename would risk routing + orphaned records. Decision by Michael 2026-07-27.

## 5. Contract (the engine's interface)

`run_sdr_engine(brand_key: str, source: str = "permit_feed", commit: bool = False) -> dict`

- `commit=False` (DEFAULT) = shadow mode: read-only, side-effect-free, produces a digest of what it WOULD do. `commit=True` = live-write to CRM (still never SENDS outreach; sending is sub-project #4).
- Returns `{"produced": int, "pass": int, "needs_human": int, "fail": int, "written": int, "digest_path": str}`.
- Entry: `POST /admin/run-sdr` (dry-run default) mirroring `POST /admin/run-social`; schedule a daily Railway cron once shadow proves out.

## 6. The loop (one run)

1. **Pull fresh signals** from `permit_feed` for `brand_key`. Bucket by recency (principle 9): MOVE TODAY (<14d), THIS MONTH (15-45d), drop >45d. Work newest first.
2. **Dedup** against what the CRM already has and what a prior run already produced (principle: never double-create). Skip anything already an opportunity or in-sequence.
3. **Each candidate → `sdr_verification_gate.run(...)`** with `motion="rebuild"`, the resolved `business_key`, and the permit-derived entity.
4. **Route by the gate's returned verdict/queue_status:**
   - PASS auto_approved → if `commit`, `crm_router.push_prospects_to_crm(...)` to the resolved brand CRM; if shadow, record "would write".
   - NEEDS_HUMAN → `approval_queue` pending (already handled inside the gate's `run()`); recorded for the digest.
   - FAIL → dropped + logged, never written.
5. **Produce the digest** (always, both modes): a dated markdown receipt — per candidate: business, real primary site, verdict, the verified defect + evidence, contact, and (shadow) what it would have written. Mirror `studio_social_engine`'s batch/receipt shape. Deliver per the existing receipt path (committed receipt + the engine-output watchdog picks it up; email to Michael optional).

## 7. Shadow -> live ramp (Michael's requirement, encoded)

- **Shadow (default, `commit=False`):** every run produces the digest, writes nothing. Michael reads the digests. The gate's verify-rate and the digest quality are the proof.
- **Live-write (`commit=True`):** flip only when the shadow digests are consistently correct. Even live, the engine only WRITES verified opportunities to CRM; it never SENDS outreach — that is sub-project #4, behind its own switch and the `approval_queue`.
- No auto-flip. The mode is an explicit config/flag change by Michael, exactly like the existing engines' dry-run default.

## 8. File layout

- `services/sdr_engine.py` — the engine (`run_sdr_engine`, the loop, the digest).
- business_key normalization: a small `_resolve_business_key` in the engine (or a shared helper) — §4.
- `POST /admin/run-sdr` route — wherever `/admin/run-social` is registered.
- `tests/test_sdr_engine.py` — see Testing.
- Reuses unchanged: `sdr_verification_gate`, `permit_feed`, `crm_router`, `approval_queue`, `runtime`.

## 9. Testing

- **Shadow mode writes nothing:** a run with `commit=False` over fixture permits calls neither `crm_router.push_prospects_to_crm` nor any live send; asserts the digest is produced and counts are right. (Monkeypatch the gate + crm_router; assert push NOT called.)
- **business_key routing:** `wd` resolves to the WD Twenty target (not the GHL default); `aipg` resolves to GHL; an unready CRM holds in pending rather than misrouting.
- **Verdict routing:** a PASS candidate (commit=True) calls `push_prospects_to_crm` with the resolved key; a FAIL candidate never does; a NEEDS_HUMAN candidate queues pending, never pushes.
- **Dedup:** a candidate already present is skipped, not re-created.
- **Recency bucketing:** a >45d permit is dropped; a <14d permit is worked first.
- **Ship criterion:** a full shadow run over a fixture permit batch produces a correct digest and makes zero live writes/sends.

## 10. Out of scope (explicit)

- Flipping live SEND on (sub-project #4).
- Additional signal sources beyond `permit_feed` (DataMoon intent, etc. — later).
- The research/voice/reply/sequence skills (later sub-projects, per the principles build order).
- Any change to the gate, `approval_queue` thresholds, or `crm_router`'s provider logic (only the desk->runtime key normalization is added, at the engine boundary).

## 11. Open items (owner: Michael, non-blocking for build)
1. Confirm `wd` maps to runtime `callingdigital` (vs a distinct `wd` key) and that it wires to `twenty_wd_ready` / the worshipdigital.co Twenty instance.
2. Digest delivery: committed receipt only (watchdog-surfaced), or also emailed to Michael each run.
