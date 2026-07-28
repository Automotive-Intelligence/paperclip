# SDR Verification Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a bounded module that verifies a sourced prospect's real primary site, real defect, and real contact before it becomes an opportunity, emitting a verdict that drives the existing `approval_queue`.

**Architecture:** A single module `services/sdr_verification_gate.py` exposes `verify(request) -> VerificationResult`. Three checks run in order, deterministic Python (`requests`/subprocess `curl`) for everything provable, and exactly one LLM judgment call (via the existing `studio_social_llm.llm_json` adapter) for the fuzzy "is this really their primary site" call. The verdict maps to an `approval_queue` artifact: clean auto-dispatches, ambiguous queues, garbage is dropped.

**Tech Stack:** Python 3.12, `requests`, `pytest`, existing modules `services/approval_queue`, `tools/crm_router`, `services/studio_social_llm`, `services/intent_scoring`.

## Global Constraints

- **No LLM lock-in:** deterministic checks are pure Python; the ONE judgment call goes through `services/studio_social_llm.llm_json` (the single swap point); check logic/prompt lives at `.agents/skills/prospect-verification/SKILL.md`, plain text, NOT a Claude Project.
- **Never fabricate** a company, defect, relationship, or contact number.
- **Never accept a data-broker** (RocketReach/ZoomInfo) phone or email as a verified contact.
- **Verdict → approval_queue:** all-pass + confidence >= 0.75 -> `risk_level="low"` (auto_approved); ambiguous -> `risk_level="medium"` (pending_approval); any FAIL -> dropped + logged, never queued, never contacted.
- **No em-dashes** in any prospect-facing copy the gate emits (the suggested hook).
- **Business keys:** `wd`, `avi`, `aipg`, `bookd`. CRM routing is `tools/crm_router` (GHL for `aipg`, per-brand Twenty for the rest).
- **Acceptance:** reproduce the human-correct verdicts on the seven 2026-07-15 rebuilds.

---

### Task 0: Pin the interfaces (no code, prevents inventing signatures)

**Files:**
- Read only: `services/approval_queue.py`, `tools/crm_router.py`, `tools/twenty.py`, `services/studio_social_llm.py`, `services/intent_scoring.py`

- [ ] **Step 1:** Read and record, as a comment block at the top of the new `services/sdr_verification_gate.py`, the EXACT current signatures: the `approval_queue` artifact constructor + its `queue_artifact(...)` entry and required fields (`confidence`, `risk_level`, `requires_human_approval`, `metadata`, `agent_id`/`business_key`); `crm_router.push_prospects_to_crm(prospects: list, source_agent: str, business_key: str) -> Tuple[str, list]` and the prospect-dict keys `push_prospects_to_twenty`/`push_prospects_to_ghl` actually read; and `studio_social_llm.llm_json(...)` signature. Do not guess; copy what the source says.
- [ ] **Step 2:** Commit the stub module with only that interface-note docstring. `git add services/sdr_verification_gate.py && git commit -m "chore: pin verification-gate integration interfaces"`

---

### Task 1: Types + Check 1 (real primary site resolution)

**Files:**
- Modify: `services/sdr_verification_gate.py`
- Test: `tests/test_sdr_verification_gate.py`
- Create fixtures: `tests/fixtures/verification/` (raw curl-header captures)

**Interfaces:**
- Produces: `@dataclass VerificationRequest(business_key:str, entity:dict, signal, motion:str)`; `@dataclass VerificationResult(verdict:str, real_primary_site:Optional[str], verified_defect:Optional[dict], verified_contact:Optional[dict], confidence:float, evidence_log:list, reason:str)`; `resolve_primary_site(domain_on_file:str, company_name:str, city:str="") -> tuple[str, list[str]]` returning `(real_domain, evidence_log)`.

- [ ] **Step 1: Write the failing test** (cert-CN mismatch = alias, must resolve to the CN host; 301 must be followed)

```python
# tests/test_sdr_verification_gate.py
from services.sdr_verification_gate import resolve_primary_site

def test_cert_cn_mismatch_resolves_to_real_host(monkeypatch):
    # Bonick case: domain_on_file cert is issued to www.bonicklandscaping.com
    def fake_headers(domain):
        return {"cert_cn": "www.bonicklandscaping.com", "location": None, "status": 200}
    monkeypatch.setattr("services.sdr_verification_gate._probe_headers", fake_headers)
    real, log = resolve_primary_site("bonick.com", "Bonick Landscaping")
    assert real == "www.bonicklandscaping.com"
    assert any("cert" in e.lower() for e in log)

def test_301_redirect_is_followed(monkeypatch):
    # Stride case: 301 to the real live site
    def fake_headers(domain):
        return {"cert_cn": domain, "location": "https://stridepestcontrol.com/", "status": 301}
    monkeypatch.setattr("services.sdr_verification_gate._probe_headers", fake_headers)
    real, log = resolve_primary_site("stridepest.com", "Stride Pest")
    assert real == "stridepestcontrol.com"
```

- [ ] **Step 2: Run test to verify it fails** — `pytest tests/test_sdr_verification_gate.py -k primary -v` → FAIL (function not defined).
- [ ] **Step 3: Write minimal implementation**

```python
# services/sdr_verification_gate.py
import subprocess, re
from dataclasses import dataclass, field
from typing import Optional

def _probe_headers(domain: str) -> dict:
    """curl -sIv: returns {cert_cn, location, status}. Pure Python, no LLM."""
    out = subprocess.run(
        ["curl", "-sIv", "--max-time", "12", f"https://{domain}"],
        capture_output=True, text=True,
    ).stderr + subprocess.run(
        ["curl", "-sI", "--max-time", "12", f"https://{domain}"],
        capture_output=True, text=True,
    ).stdout
    cn = re.search(r"subject: CN=([^\s]+)", out)
    loc = re.search(r"(?i)^location:\s*(\S+)", out, re.M)
    st = re.search(r"HTTP/\d(?:\.\d)?\s+(\d{3})", out)
    return {"cert_cn": cn.group(1) if cn else None,
            "location": loc.group(1) if loc else None,
            "status": int(st.group(1)) if st else None}

def _host(url: str) -> str:
    return re.sub(r"^https?://", "", (url or "")).split("/")[0]

def resolve_primary_site(domain_on_file: str, company_name: str, city: str = ""):
    log, current = [], domain_on_file
    for _ in range(3):  # follow up to 3 redirects
        h = _probe_headers(current)
        log.append(f"probe {current}: status={h['status']} cn={h['cert_cn']} loc={h['location']}")
        if h["location"] and _host(h["location"]) != current:
            current = _host(h["location"]); continue
        if h["cert_cn"] and _host(h["cert_cn"]) != current:
            log.append(f"cert CN {h['cert_cn']} != {current}; alias -> resolving to CN host")
            current = _host(h["cert_cn"]); continue
        break
    return current, log
```

- [ ] **Step 4: Run test to verify it passes** — `pytest tests/test_sdr_verification_gate.py -k primary -v` → PASS.
- [ ] **Step 5: Commit** — `git add services/sdr_verification_gate.py tests/test_sdr_verification_gate.py && git commit -m "feat: verification gate check 1 (real primary site)"`

---

### Task 2: Check 2 (real defect on the resolved primary site, rebuild motion)

**Files:** Modify `services/sdr_verification_gate.py`; Test `tests/test_sdr_verification_gate.py`

**Interfaces:**
- Consumes: `resolve_primary_site` from Task 1.
- Produces: `check_defect(real_domain:str, motion:str) -> Optional[dict]` returning `{"kind":..., "evidence":...}` or `None` (no verifiable defect).

- [ ] **Step 1: Write the failing test**

```python
from services.sdr_verification_gate import check_defect

def test_pinch_zoom_block_is_a_defect(monkeypatch):
    monkeypatch.setattr("services.sdr_verification_gate._fetch_html",
                        lambda d: '<meta name="viewport" content="width=device-width, maximum-scale=1.0">')
    monkeypatch.setattr("services.sdr_verification_gate._ttfb", lambda d: 0.4)
    monkeypatch.setattr("services.sdr_verification_gate._probe_headers",
                        lambda d: {"status": 200, "cert_cn": d, "location": None})
    d = check_defect("excaliburpest.com", "rebuild")
    assert d and d["kind"] == "pinch_zoom_blocked"
    assert "maximum-scale" in d["evidence"]

def test_healthy_site_has_no_defect(monkeypatch):
    monkeypatch.setattr("services.sdr_verification_gate._fetch_html",
                        lambda d: '<a href="tel:5551234567">Call</a><form></form>')
    monkeypatch.setattr("services.sdr_verification_gate._ttfb", lambda d: 0.3)
    monkeypatch.setattr("services.sdr_verification_gate._probe_headers",
                        lambda d: {"status": 200, "cert_cn": d, "location": None})
    assert check_defect("bonicklandscaping.com", "rebuild") is None
```

- [ ] **Step 2: Run to verify it fails** — `pytest -k defect -v` → FAIL.
- [ ] **Step 3: Write minimal implementation**

```python
import requests

def _fetch_html(domain: str) -> str:
    return requests.get(f"https://{domain}", timeout=12, allow_redirects=True).text

def _ttfb(domain: str) -> float:
    out = subprocess.run(["curl","-s","-o","/dev/null","-w","%{time_starttransfer}",
                          "--max-time","20", f"https://{domain}"], capture_output=True, text=True).stdout
    try: return float(out)
    except ValueError: return 0.0

def check_defect(real_domain: str, motion: str):
    if motion != "rebuild":
        return {"kind": f"{motion}_signal", "evidence": "signal freshness re-confirmed upstream"}
    h = _probe_headers(real_domain)
    if h["status"] in (404, 410, 500, 502, 503) or h["status"] is None:
        return {"kind": "site_down", "evidence": f"HTTP {h['status']} on https://{real_domain}"}
    html = _fetch_html(real_domain).lower()
    if re.search(r"maximum-scale=1|user-scalable=no", html):
        m = re.search(r'viewport[^>]*content="[^"]*"', html)
        return {"kind": "pinch_zoom_blocked", "evidence": m.group(0) if m else "maximum-scale=1"}
    has_tel = "tel:" in html
    has_form = "<form" in html
    has_cta = any(k in html for k in ("get a quote","contact us","call now","get started"))
    if not (has_tel or has_form or has_cta):
        return {"kind": "no_contact_path", "evidence": "no tel:, no <form>, no CTA on homepage"}
    ttfb = _ttfb(real_domain)
    if ttfb > 1.5:
        return {"kind": "slow_load", "evidence": f"TTFB {ttfb:.2f}s (curl time_starttransfer)"}
    return None
```

- [ ] **Step 4: Run to verify it passes** — `pytest -k defect -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat: verification gate check 2 (real defect)"`

---

### Task 3: Check 3 (real contact from published info, reject broker)

**Files:** Modify `services/sdr_verification_gate.py`; Test `tests/test_sdr_verification_gate.py`

**Interfaces:**
- Produces: `check_contact(entity:dict, real_domain:str) -> Optional[dict]` returning `{"name":..., "phone":..., "source":"site|gbp|yelp"}` or `None`.

- [ ] **Step 1: Write the failing test**

```python
from services.sdr_verification_gate import check_contact

def test_phone_from_site_is_verified(monkeypatch):
    monkeypatch.setattr("services.sdr_verification_gate._fetch_html",
                        lambda d: 'Call us <a href="tel:+12813536366">(281) 353-6366</a>')
    c = check_contact({"contact_name": "Craig"}, "excaliburpest.com")
    assert c and c["phone"] == "+12813536366" and c["source"] == "site"

def test_broker_only_contact_is_rejected(monkeypatch):
    monkeypatch.setattr("services.sdr_verification_gate._fetch_html", lambda d: "no phone here")
    # entity carries a broker-sourced phone; must NOT be accepted
    assert check_contact({"contact_name": "X", "contact_phone": "+15550001111",
                          "phone_source": "rocketreach"}, "example.com") is None
```

- [ ] **Step 2: Run to verify it fails** — `pytest -k contact -v` → FAIL.
- [ ] **Step 3: Write minimal implementation**

```python
def check_contact(entity: dict, real_domain: str):
    html = _fetch_html(real_domain)
    tel = re.search(r'tel:(\+?\d[\d\-\(\) ]{9,})', html)
    if tel:
        phone = re.sub(r"[^\d+]", "", tel.group(1))
        return {"name": entity.get("contact_name"), "phone": phone, "source": "site"}
    # entity phone only trusted if its source is the company's own publishing, never a broker
    src = (entity.get("phone_source") or "").lower()
    if entity.get("contact_phone") and src in ("site", "gbp", "yelp"):
        return {"name": entity.get("contact_name"), "phone": entity["contact_phone"], "source": src}
    return None  # broker-only or none -> unverified
```

- [ ] **Step 4: Run to verify it passes** — `pytest -k contact -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat: verification gate check 3 (real contact, reject broker)"`

---

### Task 4: Verdict + the one LLM judgment call + portable skill file

**Files:** Modify `services/sdr_verification_gate.py`; Create `.agents/skills/prospect-verification/SKILL.md`; Test `tests/test_sdr_verification_gate.py`

**Interfaces:**
- Consumes: `resolve_primary_site`, `check_defect`, `check_contact`; `studio_social_llm.llm_json` (pinned in Task 0).
- Produces: `verify(request: VerificationRequest) -> VerificationResult`.

- [ ] **Step 1: Write the failing test** (all three pass -> PASS+high confidence; wrong-primary-site -> FAIL; ambiguous -> NEEDS_HUMAN). Use monkeypatch on the three checks and on the llm adapter so no network runs.

```python
from services.sdr_verification_gate import verify, VerificationRequest

def _req(**kw):
    base = dict(business_key="wd", entity={"domain_on_file":"x.com","company_name":"X"},
                signal=None, motion="rebuild")
    base.update(kw); return VerificationRequest(**base)

def test_all_pass_is_high_confidence_pass(monkeypatch):
    monkeypatch.setattr("services.sdr_verification_gate.resolve_primary_site", lambda d,c,city="": ("x.com", ["same"]))
    monkeypatch.setattr("services.sdr_verification_gate.check_defect", lambda d,m: {"kind":"slow_load","evidence":"TTFB 2.3s"})
    monkeypatch.setattr("services.sdr_verification_gate.check_contact", lambda e,d: {"name":"A","phone":"+1555","source":"site"})
    monkeypatch.setattr("services.sdr_verification_gate._primary_is_our_domain", lambda dof, real: True)
    r = verify(_req())
    assert r.verdict == "PASS" and r.confidence >= 0.75

def test_real_site_elsewhere_is_fail(monkeypatch):
    monkeypatch.setattr("services.sdr_verification_gate.resolve_primary_site", lambda d,c,city="": ("realsite.com", ["alias"]))
    monkeypatch.setattr("services.sdr_verification_gate._primary_is_our_domain", lambda dof, real: False)
    monkeypatch.setattr("services.sdr_verification_gate.check_defect", lambda d,m: None)
    r = verify(_req())
    assert r.verdict == "FAIL"
```

- [ ] **Step 2: Run to verify it fails** — `pytest -k verify -v` → FAIL.
- [ ] **Step 3: Write minimal implementation** (deterministic verdict; the llm_json call is used ONLY when the primary-site call is genuinely ambiguous, e.g. a 404 that could be down-or-wrong-domain)

```python
@dataclass
class VerificationRequest:
    business_key: str; entity: dict; signal: object; motion: str

@dataclass
class VerificationResult:
    verdict: str; real_primary_site: Optional[str]; verified_defect: Optional[dict]
    verified_contact: Optional[dict]; confidence: float
    evidence_log: list = field(default_factory=list); reason: str = ""

def _primary_is_our_domain(domain_on_file: str, real: str) -> bool:
    return _host(domain_on_file) == _host(real)

def verify(req: VerificationRequest) -> VerificationResult:
    dof = req.entity.get("domain_on_file", "")
    real, log = resolve_primary_site(dof, req.entity.get("company_name",""), req.entity.get("city",""))
    defect = check_defect(real, req.motion)
    contact = check_contact(req.entity, real)

    # rebuild motion: if their real primary site is NOT the one on file, there is nothing to fix -> FAIL
    if req.motion == "rebuild" and not _primary_is_our_domain(dof, real):
        return VerificationResult("FAIL", real, None, contact, 0.0, log,
                                  f"real primary site is {real}, not {dof}; not a rebuild target")
    if req.motion == "rebuild" and defect is None:
        return VerificationResult("FAIL", real, None, contact, 0.0, log, "no verifiable defect on primary site")

    # ambiguous primary-site (e.g. site down): ONE llm judgment call, must cite evidence
    ambiguous = req.motion == "rebuild" and defect and defect["kind"] == "site_down"
    if ambiguous:
        from services.studio_social_llm import llm_json
        j = llm_json(_skill_prompt(req, real, defect, log))  # portable prompt, swappable adapter
        log.append(f"llm judgment: {j.get('rationale','')}")
        if j.get("verdict") == "NEEDS_HUMAN":
            return VerificationResult("NEEDS_HUMAN", real, defect, contact, 0.5, log, j.get("rationale",""))

    if contact is None:
        return VerificationResult("NEEDS_HUMAN", real, defect, contact, 0.5, log, "contact unverified (no published number)")

    return VerificationResult("PASS", real, defect, contact, 0.85, log, "all checks passed")

def _skill_prompt(req, real, defect, log) -> str:
    from pathlib import Path
    tmpl = Path(__file__).parent.parent / ".agents/skills/prospect-verification/SKILL.md"
    return tmpl.read_text() + f"\n\nDOMAIN_ON_FILE: {req.entity.get('domain_on_file')}\nRESOLVED: {real}\nDEFECT: {defect}\nEVIDENCE:\n" + "\n".join(log)
```

- [ ] **Step 4:** Create `.agents/skills/prospect-verification/SKILL.md` with the judgment prompt: "You are given a resolved primary domain, a candidate defect, and the raw evidence log. Decide if a site returning an error is genuinely down (a real rebuild target) or a wrong/parked domain (NEEDS_HUMAN). Return JSON `{verdict: 'PASS'|'NEEDS_HUMAN', rationale}`. Cite the evidence. Never invent a defect the evidence does not show." (Plain text, no Claude-Project coupling.)
- [ ] **Step 5: Run to verify it passes** — `pytest -k verify -v` → PASS. **Commit** — `git add services/sdr_verification_gate.py .agents/skills/prospect-verification/SKILL.md tests/test_sdr_verification_gate.py && git commit -m "feat: verification gate verdict + portable judgment skill"`

---

### Task 5: Hand-off to approval_queue + CRM push on auto-approve

**Files:** Modify `services/sdr_verification_gate.py`; Test `tests/test_sdr_verification_gate.py`

**Interfaces:**
- Consumes: `verify`; `approval_queue` artifact + `queue_artifact` (pinned Task 0); `crm_router.push_prospects_to_crm` (pinned Task 0).
- Produces: `run(request) -> dict` returning `{"verdict":..., "queue_status":..., "crm":...}`.

- [ ] **Step 1: Write the failing test** (PASS -> low risk -> auto_approved -> crm push called; FAIL -> nothing queued, nothing pushed)

```python
from services.sdr_verification_gate import run, VerificationRequest, VerificationResult

def test_pass_auto_approves_and_pushes(monkeypatch):
    monkeypatch.setattr("services.sdr_verification_gate.verify",
        lambda r: VerificationResult("PASS","x.com",{"kind":"slow_load","evidence":"e"},
                                     {"name":"A","phone":"+1555","source":"site"},0.85,["log"],"ok"))
    calls = {}
    monkeypatch.setattr("services.sdr_verification_gate._queue", lambda **k: (calls.setdefault("queue",k), "auto_approved")[1])
    monkeypatch.setattr("services.sdr_verification_gate._push_crm", lambda **k: calls.setdefault("push",k) or ("twenty",[{"status":"created"}]))
    out = run(VerificationRequest("wd",{"domain_on_file":"x.com","company_name":"X"},None,"rebuild"))
    assert out["queue_status"] == "auto_approved"
    assert "push" in calls and calls["push"]["business_key"] == "wd"

def test_fail_never_queues_or_pushes(monkeypatch):
    monkeypatch.setattr("services.sdr_verification_gate.verify",
        lambda r: VerificationResult("FAIL","real.com",None,None,0.0,["log"],"real site elsewhere"))
    calls = {}
    monkeypatch.setattr("services.sdr_verification_gate._queue", lambda **k: calls.setdefault("queue",k))
    monkeypatch.setattr("services.sdr_verification_gate._push_crm", lambda **k: calls.setdefault("push",k))
    out = run(VerificationRequest("wd",{"domain_on_file":"x.com","company_name":"X"},None,"rebuild"))
    assert out["verdict"] == "FAIL" and "queue" not in calls and "push" not in calls
```

- [ ] **Step 2: Run to verify it fails** — `pytest -k "auto_approves or never_queues" -v` → FAIL.
- [ ] **Step 3: Write minimal implementation** (use the EXACT `approval_queue`/`crm_router` shapes pinned in Task 0; the wrappers `_queue`/`_push_crm` isolate them so tests can monkeypatch)

```python
def _queue(*, business_key, confidence, risk_level, content, metadata):
    from services import approval_queue
    # construct the artifact per the pinned constructor, then queue
    art = approval_queue.Artifact(  # field names pinned in Task 0
        intent="sdr_prospect", content=content, subject=None, channel_candidates=[],
        confidence=confidence, risk_level=risk_level, requires_human_approval=(risk_level!="low"),
        metadata=metadata, business_key=business_key, agent_id="sdr-verification-gate")
    return approval_queue.queue_artifact(art)

def _push_crm(*, prospects, business_key):
    from tools.crm_router import push_prospects_to_crm
    return push_prospects_to_crm(prospects, source_agent="sdr-verification-gate", business_key=business_key)

def run(req: VerificationRequest) -> dict:
    res = verify(req)
    if res.verdict == "FAIL":
        return {"verdict": "FAIL", "queue_status": None, "crm": None, "reason": res.reason}
    risk = "low" if (res.verdict == "PASS" and res.confidence >= 0.75) else "medium"
    prospect = {"company_name": req.entity.get("company_name"), "domain": res.real_primary_site,
                "phone": (res.verified_contact or {}).get("phone"),
                "name": (res.verified_contact or {}).get("name"),
                "defect": res.verified_defect, "evidence": res.evidence_log}
    status = _queue(business_key=req.business_key, confidence=res.confidence, risk_level=risk,
                    content=prospect, metadata={"verdict": res.verdict, "reason": res.reason})
    crm = _push_crm(prospects=[prospect], business_key=req.business_key) if status == "auto_approved" else None
    return {"verdict": res.verdict, "queue_status": status, "crm": crm, "reason": res.reason}
```

- [ ] **Step 4: Run to verify it passes** — `pytest -k "auto_approves or never_queues" -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat: verification gate approval-queue + CRM hand-off"`

---

### Task 6: Golden acceptance test (the seven 2026-07-15 rebuilds)

**Files:** Test `tests/test_sdr_verification_gate_acceptance.py`; Fixtures `tests/fixtures/verification/rebuilds/*.json`

**Interfaces:** Consumes `verify`. No new production code; this is the ship gate.

- [ ] **Step 1: Capture fixtures.** For each of the 7 real domains, record the `_probe_headers`, `_fetch_html`, and `_ttfb` outputs into a JSON per business (`taps.json`, `bonick.json`, `stride.json`, `spike.json`, `excalibur.json`, `poolology.json`, `poolpros.json`). These make the test offline + deterministic.
- [ ] **Step 2: Write the acceptance test**

```python
import json, glob, pytest
from services.sdr_verification_gate import verify, VerificationRequest

EXPECTED = {"spike":"PASS","excalibur":"PASS","poolology":"PASS",
            "bonick":"FAIL","stride":"FAIL","taps":"NEEDS_HUMAN","poolpros":"NEEDS_HUMAN"}

@pytest.mark.parametrize("name,expected", EXPECTED.items())
def test_rebuild_verdict_matches_human(name, expected, monkeypatch):
    fx = json.load(open(f"tests/fixtures/verification/rebuilds/{name}.json"))
    monkeypatch.setattr("services.sdr_verification_gate._probe_headers", lambda d: fx["headers"])
    monkeypatch.setattr("services.sdr_verification_gate._fetch_html", lambda d: fx["html"])
    monkeypatch.setattr("services.sdr_verification_gate._ttfb", lambda d: fx["ttfb"])
    monkeypatch.setattr("services.studio_social_llm.llm_json", lambda p: fx.get("llm", {"verdict":"NEEDS_HUMAN","rationale":"ambiguous"}))
    r = verify(VerificationRequest("wd", fx["entity"], None, "rebuild"))
    assert r.verdict == expected, f"{name}: got {r.verdict} ({r.reason})"
```

- [ ] **Step 3: Run** — `pytest tests/test_sdr_verification_gate_acceptance.py -v`. Expected: 7 PASS. If any mismatches, the gate does not ship until the check logic is corrected (fix the check, not the expectation).
- [ ] **Step 4: Commit** — `git add tests/test_sdr_verification_gate_acceptance.py tests/fixtures/verification/rebuilds && git commit -m "test: verification gate reproduces the 7-rebuild human verdicts (ship gate)"`

---

## Self-Review

- **Spec coverage:** §5 check 1 -> Task 1; §5 check 2 -> Task 2; §5 check 3 -> Task 3; §6 verdict+queue mapping -> Tasks 4-5; §7 no-lock-in (llm_json adapter + skill file) -> Task 4; §8 file layout -> Tasks 0-6; §9 acceptance -> Task 6. All covered.
- **Placeholders:** none; each code step is real. The one honest read-from-source step is Task 0 (pinning existing signatures), by design.
- **Type consistency:** `VerificationRequest`/`VerificationResult` defined in Task 1/4 and used identically in Tasks 4-6; `_probe_headers`/`_fetch_html`/`_ttfb` names consistent across tasks and monkeypatched by the same paths in the acceptance test.
