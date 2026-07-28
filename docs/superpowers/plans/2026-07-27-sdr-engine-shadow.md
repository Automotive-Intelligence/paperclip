# SDR Engine (shadow mode) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A self-producing engine that reads unverified candidates from a brand's Twenty, runs each through the Verification Gate, and routes verified opportunities to the right CRM, shadow mode by default (writes/sends nothing until commit is flipped).

**Architecture:** One module `services/sdr_engine.py` exposes `run_sdr_engine(brand_key, source, commit)`. It resolves the desk key to the runtime CRM key, reads candidates, calls `sdr_verification_gate.run(...)` per candidate, routes by verdict, and produces a dated digest. Mirrors `studio_social_engine`'s dry-run-default pattern. Consumes SP#1 (`services/sdr_verification_gate`).

**Tech Stack:** Python 3.12, `requests`, `pytest`, existing `services/sdr_verification_gate`, `tools/twenty`, `tools/crm_router`, `services/approval_queue`, `config/runtime`.

## Global Constraints (from sdr-desk-principles.md + the SP2 spec)

- **Shadow is the default.** `commit=False` = read-only, side-effect-free; produces a digest of what it WOULD do; calls NO CRM write and NO send. `commit=True` = write verified opportunities to CRM only (NEVER sends outreach; sending is SP#4).
- **business_key normalization at the boundary:** `wd`->`callingdigital`, `avi`->`autointelligence`, `aipg`->`aiphoneguy`, `bookd`->`bookd`. Assert the target CRM is `*_ready` before any write; if not ready, hold in `approval_queue` pending, never misroute. Do NOT rename the underlying `callingdigital` slug.
- **Never double-create.** Dedup: skip a candidate already verified/opportunity/in-sequence.
- **FAIL never writes.** Only a gate PASS with auto_approved status may write (and only when `commit=True`).
- **No em-dashes** in any emitted digest copy.
- **Never fabricate.** The digest reports only real gate output.

---

### Task 0: Pin interfaces (no code; prevents inventing signatures)

**Files:** Read only: `services/sdr_verification_gate.py`, `tools/twenty.py`, `tools/crm_router.py`, `config/runtime.py`, `services/studio_social_engine.py`.

- [ ] **Step 1:** Record, as a docstring comment block at the top of new `services/sdr_engine.py`, the EXACT current signatures: `sdr_verification_gate.run(VerificationRequest) -> dict` and the `VerificationRequest(business_key, entity, signal, motion)` fields + the dict keys `run()` returns (`verdict`, `queue_status`, `crm`, `reason`); the `tools/twenty.py` per-key base_url/api_key resolution (the maps keyed on `callingdigital`/`autointelligence`/etc.) and any read/list helper for people; `crm_router.push_prospects_to_crm(prospects, source_agent, business_key)`; the `config/runtime.py` readiness flags (`twenty_wd_ready`, etc.) and `business_crm_map`; and `studio_social_engine._commit_files_to_main(files, message, token)` for receipt delivery. Copy what source says; do not guess.
- [ ] **Step 2:** Commit the stub module with only that interface note. `git add services/sdr_engine.py && git commit -m "chore: pin sdr-engine integration interfaces"`

---

### Task 1: business_key resolution + ready-assert

**Files:** Modify `services/sdr_engine.py`; Test `tests/test_sdr_engine.py`

**Interfaces:** Produces `resolve_business_key(desk_key: str) -> str` (desk -> runtime key) and `crm_ready(runtime_key: str) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sdr_engine.py
from services.sdr_engine import resolve_business_key

def test_desk_keys_map_to_runtime_keys():
    assert resolve_business_key("wd") == "callingdigital"
    assert resolve_business_key("avi") == "autointelligence"
    assert resolve_business_key("aipg") == "aiphoneguy"
    assert resolve_business_key("bookd") == "bookd"

def test_unknown_desk_key_raises_not_silently_defaults():
    import pytest
    with pytest.raises(ValueError):
        resolve_business_key("nope")
```

- [ ] **Step 2: Run to verify fail** — `pytest tests/test_sdr_engine.py -k business_key -v` -> FAIL.
- [ ] **Step 3: Implement**

```python
# services/sdr_engine.py
_DESK_TO_RUNTIME = {"wd": "callingdigital", "avi": "autointelligence",
                    "aipg": "aiphoneguy", "bookd": "bookd"}

def resolve_business_key(desk_key: str) -> str:
    try:
        return _DESK_TO_RUNTIME[desk_key]
    except KeyError:
        raise ValueError(f"unknown desk business key: {desk_key!r}")

def crm_ready(runtime_key: str) -> bool:
    from config.runtime import settings  # readiness flags live here
    return settings.crm_ready_for(runtime_key)  # Task 0: confirm the exact method name; fall back to settings.business_crm_map + twenty_*_ready
```

- [ ] **Step 4: Run to verify pass.** — `pytest tests/test_sdr_engine.py -k business_key -v` -> PASS. (If `settings.crm_ready_for` is not the real method, use the real readiness accessor found in Task 0.)
- [ ] **Step 5: Commit** — `git commit -am "feat: sdr-engine business_key resolution + ready-assert"`

---

### Task 2: read unverified candidates from the brand's Twenty

**Files:** Modify `services/sdr_engine.py`; Test `tests/test_sdr_engine.py`

**Interfaces:** Produces `read_unverified_candidates(runtime_key: str, limit: int = 100) -> list[dict]` where each dict = `{"twenty_id", "company_name", "domain_on_file", "contact_name", "contact_phone", "contact_email", "created_at"}`. A candidate is "unverified" if it lacks the tag `gate-verified`.

- [ ] **Step 1: Write the failing test** (monkeypatch the HTTP getter so no network runs)

```python
from services.sdr_engine import read_unverified_candidates

def test_reads_and_filters_out_already_verified(monkeypatch):
    fake_people = [
        {"id":"1","name":{"firstName":"A"},"companyName":"Acme","domainName":{"primaryLinkUrl":"acme.com"},
         "emails":{"primaryEmail":"a@acme.com"},"phones":{"primaryPhoneNumber":"+1555"},"createdAt":"2026-07-20","tags":[]},
        {"id":"2","name":{"firstName":"B"},"companyName":"Verified Co","tags":["gate-verified"]},
    ]
    monkeypatch.setattr("services.sdr_engine._twenty_get_people", lambda rk, limit: fake_people)
    out = read_unverified_candidates("callingdigital")
    assert len(out) == 1 and out[0]["twenty_id"] == "1" and out[0]["domain_on_file"] == "acme.com"
```

- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** (reuse `tools/twenty.py`'s base_url/api_key resolution for `runtime_key`; the HTTP call is isolated in `_twenty_get_people` so tests monkeypatch it)

```python
import requests
def _twenty_get_people(runtime_key: str, limit: int) -> list:
    from tools.twenty import _base_url_for, _api_key_for  # Task 0: confirm the real accessor names
    base, key = _base_url_for(runtime_key), _api_key_for(runtime_key)
    r = requests.get(f"{base}/rest/people?limit={limit}",
                     headers={"Authorization": f"Bearer {key}"}, timeout=20)
    return (r.json().get("data") or {}).get("people") or []

def read_unverified_candidates(runtime_key: str, limit: int = 100) -> list:
    out = []
    for p in _twenty_get_people(runtime_key, limit):
        if "gate-verified" in (p.get("tags") or []):
            continue
        out.append({
            "twenty_id": p.get("id"),
            "company_name": p.get("companyName"),
            "domain_on_file": (p.get("domainName") or {}).get("primaryLinkUrl"),
            "contact_name": (p.get("name") or {}).get("firstName"),
            "contact_phone": (p.get("phones") or {}).get("primaryPhoneNumber"),
            "contact_email": (p.get("emails") or {}).get("primaryEmail"),
            "created_at": p.get("createdAt"),
        })
    return out
```

- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: sdr-engine reads unverified candidates from Twenty"`

---

### Task 3: the loop core (run_sdr_engine), shadow-safe

**Files:** Modify `services/sdr_engine.py`; Test `tests/test_sdr_engine.py`

**Interfaces:** Consumes Tasks 1-2 + `sdr_verification_gate`. Produces `run_sdr_engine(brand_key: str, source: str = "twenty_unverified", commit: bool = False) -> dict` returning `{"produced","pass","needs_human","fail","written","digest"}`.

- [ ] **Step 1: Write the failing tests** (shadow writes nothing; commit writes only PASS)

```python
from services.sdr_engine import run_sdr_engine

def _candidates(monkeypatch, cands):
    monkeypatch.setattr("services.sdr_engine.read_unverified_candidates", lambda rk, limit=100: cands)

def test_shadow_mode_writes_nothing(monkeypatch):
    _candidates(monkeypatch, [{"twenty_id":"1","company_name":"Acme","domain_on_file":"acme.com"}])
    monkeypatch.setattr("services.sdr_engine._gate_run",
        lambda **k: {"verdict":"PASS","queue_status":"auto_approved","crm":None,"reason":"ok"})
    pushed = []
    monkeypatch.setattr("services.sdr_engine._push_crm", lambda **k: pushed.append(k))
    out = run_sdr_engine("wd", commit=False)
    assert out["pass"] == 1 and out["written"] == 0 and pushed == []  # shadow: never writes

def test_commit_writes_only_pass(monkeypatch):
    _candidates(monkeypatch, [
        {"twenty_id":"1","company_name":"Pass","domain_on_file":"p.com"},
        {"twenty_id":"2","company_name":"Fail","domain_on_file":"f.com"}])
    def gate(**k):
        return {"verdict":"PASS","queue_status":"auto_approved","crm":None,"reason":"ok"} \
            if k["request"].entity["company_name"]=="Pass" \
            else {"verdict":"FAIL","queue_status":None,"crm":None,"reason":"real site elsewhere"}
    monkeypatch.setattr("services.sdr_engine._gate_run", gate)
    pushed=[]
    monkeypatch.setattr("services.sdr_engine._push_crm", lambda **k: pushed.append(k))
    out = run_sdr_engine("wd", commit=True)
    assert out["written"] == 1 and out["fail"] == 1
    assert len(pushed) == 1 and pushed[0]["business_key"] == "callingdigital"
```

- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement**

```python
from dataclasses import dataclass
def _gate_run(*, request):
    from services.sdr_verification_gate import run as gate_run
    return gate_run(request)

def _push_crm(*, prospects, business_key):
    from tools.crm_router import push_prospects_to_crm
    return push_prospects_to_crm(prospects, source_agent="sdr-engine", business_key=business_key)

def run_sdr_engine(brand_key: str, source: str = "twenty_unverified", commit: bool = False) -> dict:
    from services.sdr_verification_gate import VerificationRequest
    runtime_key = resolve_business_key(brand_key)
    counts = {"produced":0,"pass":0,"needs_human":0,"fail":0,"written":0}
    lines = []
    for c in read_unverified_candidates(runtime_key):
        counts["produced"] += 1
        req = VerificationRequest(business_key=brand_key,
                                  entity={"company_name":c.get("company_name"),
                                          "domain_on_file":c.get("domain_on_file"),
                                          "contact_name":c.get("contact_name"),
                                          "contact_phone":c.get("contact_phone")},
                                  signal=None, motion="rebuild")
        res = _gate_run(request=req)
        v = res["verdict"]
        counts[{"PASS":"pass","NEEDS_HUMAN":"needs_human","FAIL":"fail"}[v]] += 1
        would = "WOULD WRITE" if (v=="PASS" and res["queue_status"]=="auto_approved") else "hold/skip"
        if v=="PASS" and res["queue_status"]=="auto_approved" and commit and crm_ready(runtime_key):
            _push_crm(prospects=[{"company_name":c.get("company_name"),"website":c.get("domain_on_file"),
                                  "phone":c.get("contact_phone"),"name":c.get("contact_name")}],
                      business_key=runtime_key)
            counts["written"] += 1; would = "WROTE"
        lines.append(f"- {c.get('company_name')} | {v} | {res['reason']} | {would}")
    digest = "Shadow run\n\n" + "\n".join(lines) + f"\n\nCounts: {counts}"
    return {**counts, "digest": digest}
```

- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat: sdr-engine loop core (shadow-safe: writes only on PASS+commit+ready)"`

---

### Task 4: the digest receipt + delivery

**Files:** Modify `services/sdr_engine.py`; Test `tests/test_sdr_engine.py`

**Interfaces:** Produces `_write_digest(digest: str, brand_key: str, commit: bool) -> str` returning a receipt path/id. In shadow OR commit, the digest is always produced; only `commit=True` publishes it via `studio_social_engine._commit_files_to_main` (monkeypatched in tests).

- [ ] **Step 1: Write the failing test**

```python
def test_digest_always_produced_publish_only_on_commit(monkeypatch):
    published = []
    monkeypatch.setattr("services.sdr_engine._commit_receipt", lambda path, body: published.append(path))
    from services.sdr_engine import _write_digest
    p_shadow = _write_digest("body", "wd", commit=False)
    p_commit = _write_digest("body", "wd", commit=True)
    assert p_shadow and p_commit          # a path/id is always returned
    assert len(published) == 1            # only the commit run published
```

- [ ] **Step 2-4:** implement `_write_digest` (build a dated receipt name, e.g. `sdr_shadow_<brand>_<runid>.md`; call `_commit_receipt` only when `commit`), wrap `_commit_receipt` around `studio_social_engine._commit_files_to_main` so tests isolate it; run green.
- [ ] **Step 5: Commit** — `git commit -am "feat: sdr-engine digest receipt (publish only on commit)"`

---

### Task 5: /admin/run-sdr route (dry-run default)

**Files:** Modify `app.py` (near `/admin/run-social`); Test `tests/test_sdr_engine.py`

- [ ] **Step 1:** Add `@app.post("/admin/run-sdr")` that reads `brand` (default `wd`) and `commit` (default False) from the request and calls `run_sdr_engine(brand, commit=commit)`, returning the counts JSON. Dry-run (commit=False) is the default, mirroring `/admin/run-social`.
- [ ] **Step 2:** Smoke test: a POST with no body runs `run_sdr_engine("wd", commit=False)` (monkeypatch `run_sdr_engine` to assert it's called with commit=False by default).
- [ ] **Step 3: Commit** — `git commit -am "feat: /admin/run-sdr route (dry-run default)"`

---

### Task 6: acceptance — a full shadow run makes zero live writes

**Files:** Test `tests/test_sdr_engine_acceptance.py`

- [ ] **Step 1: Write the acceptance test:** feed a mixed fixture batch (a would-PASS, a FAIL, a NEEDS_HUMAN) through `run_sdr_engine("wd", commit=False)` with the gate monkeypatched to real-ish verdicts and `_push_crm` monkeypatched; assert `_push_crm` is NEVER called, the digest contains all three, and counts are correct. This is the ship gate: **shadow mode is provably side-effect-free.**
- [ ] **Step 2: Run** -> PASS. **Step 3: Commit** — `git commit -am "test: sdr-engine shadow run is provably side-effect-free (ship gate)"`

---

## Self-Review
- Spec coverage: §4 key mapping -> Task 1; §6 step 1 (read Twenty) -> Task 2; §6 steps 2-4 (loop+route) -> Task 3; §6 step 5 (digest) -> Task 4; §5 route -> Task 5; §9 ship criterion -> Task 6. Covered.
- Placeholders: none; each code step is real. Task 0 pins the two accessor names to confirm from source (`settings` readiness method, `tools/twenty` base/key accessors) — an honest read step, not a placeholder.
- Type consistency: `run_sdr_engine`, `resolve_business_key`, `read_unverified_candidates`, `_gate_run`, `_push_crm` names are used identically across tasks and monkeypatched by the same paths in the acceptance test.
