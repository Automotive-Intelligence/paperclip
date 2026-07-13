# The Pipe (social_load) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One loader every scheduled social post rides through — Zernio for own brands, Buffer drafts for P&P — with a live queue-collision guard, UTM tagging on every link, and a post registry for attribution.

**Architecture:** New module `tools/social_load.py` holds pure helpers (UTM, registry, routing, conflict detection) plus a `load_jobs()` orchestrator with injectable rail clients (real clients = `tools/zernio.py` functions and `tools/buffer.py` `.func` callables). `studio_publish.py` keeps its batch parsing, gate refusal, and stagger grid but delegates scheduling to `load_jobs()`. The blog-engine prompt (avo-telemetry repo) is edited to call the new CLI.

**Tech Stack:** Python 3.12, stdlib only in the new module (json/os/re/dataclasses/urllib.parse), unittest tests run via pytest, following `tests/test_buffer_tools.py` conventions (`@tool` functions called via `.func`).

## Global Constraints

- Spec: `~/avo-telemetry/marketing_deliverables/121_social_publishing_os.md` (Phase 1).
- DRY-RUN by default everywhere; nothing schedules without `commit=True` / `--commit`.
- WD hard block: refuse `brand="wd"` while `config/social_load.json` has `"wd_rename_done": false`.
- P&P is DRAFTS ONLY (`buffer_create_draft_post`, `saveToDraft=true`); the draft is the client gate.
- UTM scheme (verbatim): `utm_source=<platform>`, `utm_medium=social`, `utm_campaign=<brand>_<content_id>`, `utm_content=<entry_point>-<slot>`.
- Registry path: `~/avo-telemetry/social_registry.jsonl`, override with env `SOCIAL_REGISTRY_PATH` (tests MUST override).
- No em-dashes in any user-visible copy this plan touches.
- AIPG→GHL legacy social path: ALREADY disabled in `app.py` (410 + `AIPG_GHL_SOCIAL_ENABLED` guard, verified 2026-07-13). No work here.
- Work on branch `feat/social-load-pipe` in `~/paperclip`; commit after every task.

---

### Task 1: UTM tagging helpers

**Files:**
- Create: `tools/social_load.py`
- Test: `tests/test_social_load.py`

**Interfaces:**
- Produces: `add_utm(url: str, platform: str, brand: str, content_id: str, entry_point: str, slot: str) -> str` and `tag_links(text: str, platform: str, brand: str, content_id: str, entry_point: str, slot: str) -> str` (rewrites every http(s) URL in a post body; leaves mailto/tel/bare-domain-without-scheme alone).

- [ ] **Step 1: Write the failing tests**

```python
"""tests/test_social_load.py — unit tests for the one social loader."""
from __future__ import annotations

import json
import os
import unittest


class TestUtm(unittest.TestCase):
    def test_add_utm_basic(self):
        from tools.social_load import add_utm
        out = add_utm("https://theaiphoneguy.com/blog/missed-call",
                      platform="facebook", brand="aipg",
                      content_id="missed-call", entry_point="blog_engine", slot="1")
        self.assertIn("utm_source=facebook", out)
        self.assertIn("utm_medium=social", out)
        self.assertIn("utm_campaign=aipg_missed-call", out)
        self.assertIn("utm_content=blog_engine-1", out)
        self.assertTrue(out.startswith("https://theaiphoneguy.com/blog/missed-call?"))

    def test_add_utm_preserves_existing_query(self):
        from tools.social_load import add_utm
        out = add_utm("https://x.co/p?ref=abc", "twitter", "avi", "c1", "adhoc", "0")
        self.assertIn("ref=abc", out)
        self.assertIn("utm_source=twitter", out)

    def test_tag_links_rewrites_all_urls_in_text(self):
        from tools.social_load import tag_links
        text = "Read https://a.com/x and https://b.com/y today"
        out = tag_links(text, "linkedin", "wd", "post9", "studio", "2")
        self.assertEqual(out.count("utm_source=linkedin"), 2)

    def test_tag_links_leaves_plain_text_alone(self):
        from tools.social_load import tag_links
        text = "Call (817) 670-9689 today. worshipdigital.co"
        self.assertEqual(tag_links(text, "facebook", "wd", "c", "studio", "0"), text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/paperclip && python3 -m pytest tests/test_social_load.py -v`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'tools.social_load'` (or ImportError).

- [ ] **Step 3: Write minimal implementation**

```python
"""tools/social_load.py — THE one social loader (file 121, Phase 1: The Pipe).

Every scheduled social post rides through load_jobs(): Zernio for own brands,
Buffer DRAFTS for P&P (the draft is the client gate). Guarantees:
  * DRY-RUN by default; commit is explicit.
  * Queue guard: refuses a post when the rail already has one for the same
    brand+platform+day (override: allow_stack=True).
  * UTM discipline: every http(s) link in every post is tagged. No UTM, no schedule.
  * Registry: one JSONL row per scheduled post (the Phase-3 attribution join key).
  * WD hard block until config/social_load.json wd_rename_done flips true.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

_URL_RE = re.compile(r"https?://[^\s\)\]\}>\"']+")


def add_utm(url: str, platform: str, brand: str, content_id: str,
            entry_point: str, slot: str) -> str:
    parts = urlsplit(url)
    q = parse_qsl(parts.query, keep_blank_values=True)
    q += [("utm_source", platform), ("utm_medium", "social"),
          ("utm_campaign", f"{brand}_{content_id}"),
          ("utm_content", f"{entry_point}-{slot}")]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


def tag_links(text: str, platform: str, brand: str, content_id: str,
              entry_point: str, slot: str) -> str:
    return _URL_RE.sub(
        lambda m: add_utm(m.group(0), platform, brand, content_id, entry_point, slot),
        text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/paperclip && python3 -m pytest tests/test_social_load.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd ~/paperclip && git add tools/social_load.py tests/test_social_load.py && git commit -m "feat(social_load): UTM tagging helpers (file 121 Phase 1)"
```

---

### Task 2: Post registry append

**Files:**
- Modify: `tools/social_load.py`
- Test: `tests/test_social_load.py` (append a TestCase)

**Interfaces:**
- Produces: `registry_path() -> str` (env `SOCIAL_REGISTRY_PATH` else `~/avo-telemetry/social_registry.jsonl`) and `append_registry(row: dict) -> None` (appends one JSON line, creates parent dir, adds `ts` ISO if missing).

- [ ] **Step 1: Write the failing test**

```python
import tempfile


class TestRegistry(unittest.TestCase):
    def test_append_registry_writes_jsonl(self):
        from tools.social_load import append_registry
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "reg.jsonl")
            os.environ["SOCIAL_REGISTRY_PATH"] = path
            try:
                append_registry({"brand": "avi", "platform": "twitter", "post_id": "p1"})
                append_registry({"brand": "avi", "platform": "linkedin", "post_id": "p2"})
                rows = [json.loads(l) for l in open(path)]
            finally:
                del os.environ["SOCIAL_REGISTRY_PATH"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["post_id"], "p1")
        self.assertIn("ts", rows[0])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_social_load.py::TestRegistry -v`
Expected: FAIL with ImportError (`append_registry` not defined).

- [ ] **Step 3: Write minimal implementation**

```python
from datetime import datetime, timezone as _tz


def registry_path() -> str:
    return os.environ.get("SOCIAL_REGISTRY_PATH") or os.path.expanduser(
        "~/avo-telemetry/social_registry.jsonl")


def append_registry(row: dict) -> None:
    path = registry_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out = {"ts": datetime.now(_tz.utc).isoformat(timespec="seconds"), **row}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False) + "\n")
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_social_load.py -v` — Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -u tools tests && git commit -m "feat(social_load): post registry JSONL append"
```

---

### Task 3: Brand routing + WD hard block + config

**Files:**
- Create: `config/social_load.json`
- Modify: `tools/social_load.py`
- Test: `tests/test_social_load.py`

**Interfaces:**
- Produces: `route_for_brand(brand: str, cfg: Optional[dict] = None) -> str` returning `"zernio"` or `"buffer"`, raising `WdBlockedError` for `wd` while `wd_rename_done` is false, raising `NoRailError` for `bookd`/`book'd`. `load_config() -> dict` reads `config/social_load.json`. Brand keys accepted lowercase: `avi`, `aipg`, `wd`, `agent_empire`, `bookd`, `paperandpurpose` (aliases: full display names, lowercased, map in `BRAND_ALIASES`).

- [ ] **Step 1: Create the config file**

```json
{
  "_comment": "file-121 Pipe config. wd_rename_done flips true ONLY after the file-120 handle-rename runbook completes; until then the loader refuses WD content (never post WD assets to @callingdigital).",
  "wd_rename_done": false
}
```

Save as `config/social_load.json`.

- [ ] **Step 2: Write the failing tests**

```python
class TestRouting(unittest.TestCase):
    def test_own_brand_routes_zernio(self):
        from tools.social_load import route_for_brand
        self.assertEqual(route_for_brand("avi"), "zernio")
        self.assertEqual(route_for_brand("Automotive Intelligence"), "zernio")

    def test_pp_routes_buffer(self):
        from tools.social_load import route_for_brand
        self.assertEqual(route_for_brand("paperandpurpose"), "buffer")
        self.assertEqual(route_for_brand("Paper & Purpose"), "buffer")

    def test_wd_blocked_until_rename(self):
        from tools.social_load import route_for_brand, WdBlockedError
        with self.assertRaises(WdBlockedError):
            route_for_brand("wd", cfg={"wd_rename_done": False})
        self.assertEqual(route_for_brand("wd", cfg={"wd_rename_done": True}), "zernio")

    def test_bookd_has_no_rail(self):
        from tools.social_load import route_for_brand, NoRailError
        with self.assertRaises(NoRailError):
            route_for_brand("bookd")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_social_load.py::TestRouting -v` — Expected: ImportError.

- [ ] **Step 4: Write minimal implementation**

```python
class WdBlockedError(RuntimeError):
    """WD content refused: handles are still @callingdigital (file 120)."""


class NoRailError(RuntimeError):
    """Brand has no distribution rail yet (e.g. Book'd until Ryan's key)."""


BRAND_ALIASES = {
    "automotive intelligence": "avi",
    "the ai phone guy": "aipg", "ai phone guy": "aipg",
    "worship digital": "wd",
    "agent empire": "agent_empire",
    "book'd": "bookd",
    "paper & purpose": "paperandpurpose", "paper and purpose": "paperandpurpose",
}
_CFG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "config", "social_load.json")


def canonical_brand(brand: str) -> str:
    b = brand.strip().lower()
    return BRAND_ALIASES.get(b, b)


def load_config() -> dict:
    try:
        return json.load(open(_CFG_PATH, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def route_for_brand(brand: str, cfg: Optional[dict] = None) -> str:
    b = canonical_brand(brand)
    if b == "paperandpurpose":
        return "buffer"
    if b == "bookd":
        raise NoRailError("Book'd has no rail: Ryan posts himself or issues a scoped key (file 121).")
    if b == "wd":
        conf = cfg if cfg is not None else load_config()
        if not conf.get("wd_rename_done"):
            raise WdBlockedError(
                "WD is hard-blocked: handles are still @callingdigital. "
                "Complete marketing_deliverables/120_wd_handle_rename_runbook.md, "
                "then flip wd_rename_done in config/social_load.json.")
    return "zernio"
```

- [ ] **Step 5: Run tests** — `python3 -m pytest tests/test_social_load.py -v` — all pass.

- [ ] **Step 6: Commit**

```bash
git add config/social_load.json tools/social_load.py tests/test_social_load.py && git commit -m "feat(social_load): brand routing, WD hard block, config"
```

---

### Task 4: Queue-collision guard (pure)

**Files:**
- Modify: `tools/social_load.py`
- Test: `tests/test_social_load.py`

**Interfaces:**
- Produces: `find_conflicts(existing: List[dict], platform: str, day: str, account_id: Optional[str] = None) -> List[dict]`. `existing` = raw rail posts. Zernio rows: `scheduledFor` UTC ISO + `platforms: [{platform, accountId}]` where `accountId` may be an OBJECT (extract `._id`). Buffer rows: `dueAt` or `scheduledAt` + `channelId`. `day` is the LOCAL `YYYY-MM-DD` of the candidate; comparison converts rail UTC to America/Chicago before taking the date.

- [ ] **Step 1: Write the failing tests**

```python
class TestQueueGuard(unittest.TestCase):
    def test_conflict_same_platform_same_local_day(self):
        from tools.social_load import find_conflicts
        existing = [{
            "_id": "z1", "status": "scheduled",
            "scheduledFor": "2026-07-14T11:30:00.000Z",   # 06:30 CDT
            "platforms": [{"platform": "facebook", "accountId": {"_id": "acct9"}}],
        }]
        hits = find_conflicts(existing, "facebook", "2026-07-14", account_id="acct9")
        self.assertEqual([h["_id"] for h in hits], ["z1"])

    def test_no_conflict_different_day_after_tz_conversion(self):
        from tools.social_load import find_conflicts
        existing = [{
            "_id": "z2", "status": "scheduled",
            "scheduledFor": "2026-07-15T00:00:00.000Z",   # 19:00 CDT on the 14th
            "platforms": [{"platform": "instagram", "accountId": "acct9"}],
        }]
        self.assertEqual(find_conflicts(existing, "instagram", "2026-07-15", "acct9"), [])
        self.assertEqual(len(find_conflicts(existing, "instagram", "2026-07-14", "acct9")), 1)

    def test_non_scheduled_rows_ignored(self):
        from tools.social_load import find_conflicts
        existing = [{"_id": "z3", "status": "failed",
                     "scheduledFor": "2026-07-14T12:00:00.000Z",
                     "platforms": [{"platform": "facebook", "accountId": "a"}]}]
        self.assertEqual(find_conflicts(existing, "facebook", "2026-07-14", "a"), [])
```

- [ ] **Step 2: Run to verify failure** — `python3 -m pytest tests/test_social_load.py::TestQueueGuard -v` — ImportError.

- [ ] **Step 3: Implement**

```python
from zoneinfo import ZoneInfo

_CENTRAL = ZoneInfo("America/Chicago")


def _rail_local_day(iso_utc: str) -> Optional[str]:
    if not iso_utc:
        return None
    try:
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(_CENTRAL).date().isoformat()


def find_conflicts(existing: List[dict], platform: str, day: str,
                   account_id: Optional[str] = None) -> List[dict]:
    hits = []
    for p in existing:
        if (p.get("status") or "").lower() not in ("scheduled", "pending", "queued", "draft"):
            continue
        when = p.get("scheduledFor") or p.get("dueAt") or p.get("scheduledAt") or ""
        if _rail_local_day(str(when)) != day:
            continue
        plats = p.get("platforms") or []
        if plats:                                    # zernio shape
            for c in plats:
                if c.get("platform") != platform:
                    continue
                aid = c.get("accountId")
                aid = aid.get("_id") if isinstance(aid, dict) else aid
                if account_id is None or str(aid) == str(account_id):
                    hits.append(p)
                    break
        else:                                        # buffer shape
            if account_id is None or str(p.get("channelId")) == str(account_id):
                hits.append(p)
    return hits
```

- [ ] **Step 4: Run all tests** — `python3 -m pytest tests/test_social_load.py -v` — all pass.

- [ ] **Step 5: Commit**

```bash
git add -u tools tests && git commit -m "feat(social_load): queue-collision guard with UTC->Central day compare"
```

---

### Task 5: load_jobs() orchestrator with injectable rails

**Files:**
- Modify: `tools/social_load.py`
- Test: `tests/test_social_load.py`

**Interfaces:**
- Produces:

```python
@dataclass
class PostJob:
    brand: str
    platform: str            # zernio id ("twitter") or buffer service ("instagram")
    content: str
    scheduled_for: str       # local ISO "YYYY-MM-DDTHH:MM:SS"
    content_id: str
    entry_point: str         # "studio" | "blog_engine" | "adhoc"
    tz: str = "America/Chicago"
    media_urls: List[str] = field(default_factory=list)
    account_id: Optional[str] = None      # zernio acct; resolved by caller (studio_publish)
    business_key: str = ""                # buffer lane (e.g. "paperandpurpose")

def load_jobs(jobs: List[PostJob], commit: bool = False, allow_stack: bool = False,
              rails: Optional[Dict[str, Any]] = None) -> List[dict]
```

  `rails` (injectable for tests; real default built lazily from tools.zernio + tools.buffer):
  `{"zernio_list": Callable[[], List[dict]], "zernio_publish": Callable[..., dict], "buffer_list": Callable[[str], List[dict]], "buffer_draft": Callable[[str, str, str], str]}`.
  Per-job result dict: `{"job": ..., "action": "scheduled"|"drafted"|"dry-run"|"blocked"|"conflict"|"error", "detail": ...}`. Every commit success appends a registry row `{brand, rail, platform, account_id, post_id, scheduled_for, tz, content_id, utm_campaign, entry_point, media_url}`.

- [ ] **Step 1: Write the failing tests**

```python
def _fake_rails(existing_zernio=None, existing_buffer=None):
    calls = {"publish": [], "draft": []}

    def zernio_list():
        return existing_zernio or []

    def zernio_publish(content, platforms, account_ids, scheduled_for, media_urls, timezone):
        calls["publish"].append(dict(content=content, platforms=platforms,
                                     account_ids=account_ids, scheduled_for=scheduled_for,
                                     media_urls=media_urls, timezone=timezone))
        return {"_id": f"zp{len(calls['publish'])}", "status": "scheduled"}

    def buffer_list(channel_id):
        return existing_buffer or []

    def buffer_draft(business_key, text, media_urls_csv):
        calls["draft"].append(dict(business_key=business_key, text=text, media=media_urls_csv))
        return json.dumps([{"channel_id": "c1", "post": {"id": "bp1", "status": "draft"}}])

    return {"zernio_list": zernio_list, "zernio_publish": zernio_publish,
            "buffer_list": buffer_list, "buffer_draft": buffer_draft}, calls


class TestLoadJobs(unittest.TestCase):
    def _job(self, **kw):
        from tools.social_load import PostJob
        base = dict(brand="avi", platform="twitter", content="Read https://a.io/p now",
                    scheduled_for="2026-07-16T07:00:00", content_id="c9",
                    entry_point="adhoc", account_id="acct1")
        base.update(kw)
        return PostJob(**base)

    def test_dry_run_calls_no_rail(self):
        from tools.social_load import load_jobs
        rails, calls = _fake_rails()
        res = load_jobs([self._job()], commit=False, rails=rails)
        self.assertEqual(res[0]["action"], "dry-run")
        self.assertEqual(calls["publish"], [])

    def test_commit_schedules_tags_utm_and_registers(self):
        from tools.social_load import load_jobs
        rails, calls = _fake_rails()
        with tempfile.TemporaryDirectory() as d:
            os.environ["SOCIAL_REGISTRY_PATH"] = os.path.join(d, "r.jsonl")
            try:
                res = load_jobs([self._job()], commit=True, rails=rails)
                rows = [json.loads(l) for l in open(os.environ["SOCIAL_REGISTRY_PATH"])]
            finally:
                del os.environ["SOCIAL_REGISTRY_PATH"]
        self.assertEqual(res[0]["action"], "scheduled")
        self.assertIn("utm_source=twitter", calls["publish"][0]["content"])
        self.assertEqual(rows[0]["post_id"], "zp1")
        self.assertEqual(rows[0]["utm_campaign"], "avi_c9")

    def test_conflict_blocks_without_allow_stack(self):
        from tools.social_load import load_jobs
        existing = [{"_id": "old", "status": "scheduled",
                     "scheduledFor": "2026-07-16T12:00:00.000Z",
                     "platforms": [{"platform": "twitter", "accountId": "acct1"}]}]
        rails, calls = _fake_rails(existing_zernio=existing)
        res = load_jobs([self._job()], commit=True, rails=rails)
        self.assertEqual(res[0]["action"], "conflict")
        self.assertEqual(calls["publish"], [])
        res2 = load_jobs([self._job()], commit=True, allow_stack=True, rails=rails)
        self.assertEqual(res2[0]["action"], "scheduled")

    def test_wd_blocked(self):
        from tools.social_load import load_jobs
        rails, calls = _fake_rails()
        res = load_jobs([self._job(brand="wd")], commit=True, rails=rails)
        self.assertEqual(res[0]["action"], "blocked")
        self.assertEqual(calls["publish"], [])

    def test_pp_goes_to_buffer_as_draft(self):
        from tools.social_load import load_jobs
        rails, calls = _fake_rails()
        with tempfile.TemporaryDirectory() as d:
            os.environ["SOCIAL_REGISTRY_PATH"] = os.path.join(d, "r.jsonl")
            try:
                res = load_jobs([self._job(brand="paperandpurpose", platform="instagram",
                                           business_key="paperandpurpose", account_id=None)],
                                commit=True, rails=rails)
            finally:
                del os.environ["SOCIAL_REGISTRY_PATH"]
        self.assertEqual(res[0]["action"], "drafted")
        self.assertEqual(len(calls["draft"]), 1)
        self.assertEqual(calls["publish"], [])
```

- [ ] **Step 2: Run to verify failure** — `python3 -m pytest tests/test_social_load.py::TestLoadJobs -v` — ImportError.

- [ ] **Step 3: Implement**

```python
def _real_rails() -> Dict[str, Any]:
    """Late imports so tests never touch the network or need API keys."""
    from tools.zernio import list_zernio_posts, publish_to_zernio
    from tools.buffer import buffer_create_draft_post, buffer_list_posts

    return {
        "zernio_list": list_zernio_posts,
        "zernio_publish": lambda **kw: publish_to_zernio(**kw),
        "buffer_list": lambda channel_id: json.loads(
            buffer_list_posts.func(channel_id, "draft", 50) or "[]"),
        "buffer_draft": lambda business_key, text, media_csv:
            buffer_create_draft_post.func(business_key, text, media_csv, ""),
    }


def load_jobs(jobs: List["PostJob"], commit: bool = False, allow_stack: bool = False,
              rails: Optional[Dict[str, Any]] = None) -> List[dict]:
    results: List[dict] = []
    zernio_queue: Optional[List[dict]] = None      # fetched once per call
    for i, job in enumerate(jobs):
        brand = canonical_brand(job.brand)
        try:
            rail = route_for_brand(brand)
        except (WdBlockedError, NoRailError) as e:
            results.append({"job": job, "action": "blocked", "detail": str(e)})
            continue

        content = tag_links(job.content, job.platform, brand, job.content_id,
                            job.entry_point, str(i))
        day = job.scheduled_for.split("T")[0]

        if rail == "zernio":
            if zernio_queue is None:
                zernio_queue = rails["zernio_list"]() if rails else _real_rails()["zernio_list"]()
            r = rails or _real_rails()
            hits = find_conflicts(zernio_queue, job.platform, day, job.account_id)
            if hits and not allow_stack:
                results.append({"job": job, "action": "conflict",
                                "detail": [h.get("_id") for h in hits]})
                continue
            if not commit:
                results.append({"job": job, "action": "dry-run", "detail": f"-> {job.scheduled_for}"})
                continue
            res = r["zernio_publish"](content=content, platforms=[job.platform],
                                      account_ids=[job.account_id] if job.account_id else None,
                                      scheduled_for=job.scheduled_for,
                                      media_urls=job.media_urls or None, timezone=job.tz)
            append_registry({"brand": brand, "rail": "zernio", "platform": job.platform,
                             "account_id": job.account_id, "post_id": res.get("_id"),
                             "scheduled_for": job.scheduled_for, "tz": job.tz,
                             "content_id": job.content_id,
                             "utm_campaign": f"{brand}_{job.content_id}",
                             "entry_point": job.entry_point,
                             "media_url": (job.media_urls or [None])[0]})
            results.append({"job": job, "action": "scheduled", "detail": res})
        else:  # buffer drafts (P&P and future clients)
            r = rails or _real_rails()
            if not commit:
                results.append({"job": job, "action": "dry-run", "detail": "buffer draft"})
                continue
            raw = r["buffer_draft"](job.business_key or brand, content,
                                    ",".join(job.media_urls))
            try:
                out = json.loads(raw)
            except ValueError:
                results.append({"job": job, "action": "error", "detail": raw[:300]})
                continue
            errs = [o for o in out if o.get("error")]
            ok = [o for o in out if o.get("post")]
            for o in ok:
                append_registry({"brand": brand, "rail": "buffer", "platform": job.platform,
                                 "account_id": o.get("channel_id"),
                                 "post_id": (o.get("post") or {}).get("id"),
                                 "scheduled_for": job.scheduled_for, "tz": job.tz,
                                 "content_id": job.content_id,
                                 "utm_campaign": f"{brand}_{job.content_id}",
                                 "entry_point": job.entry_point,
                                 "media_url": (job.media_urls or [None])[0]})
            results.append({"job": job, "action": "drafted" if ok and not errs else "error",
                            "detail": out})
    return results
```

Note: place the `PostJob` dataclass (from the Interfaces block above) directly above `load_jobs` in the module.

- [ ] **Step 4: Run the full suite** — `python3 -m pytest tests/test_social_load.py -v` — all pass.

- [ ] **Step 5: Commit**

```bash
git add -u tools tests && git commit -m "feat(social_load): load_jobs orchestrator, injectable rails, registry rows"
```

---

### Task 6: CLI for ad-hoc and engine batches

**Files:**
- Modify: `tools/social_load.py` (add `main()` + `__main__`)
- Test: `tests/test_social_load.py`

**Interfaces:**
- Consumes: `load_jobs`, `PostJob`.
- Produces: CLI `python3 tools/social_load.py jobs.json [--commit] [--allow-stack]` where jobs.json = `[{brand, platform, content, scheduled_for, content_id, entry_point, tz?, media_urls?, account_id?, business_key?}, ...]`. Prints one line per result; exit 0 if no `error`/`conflict`, else 4.

- [ ] **Step 1: Write the failing test**

```python
class TestCli(unittest.TestCase):
    def test_cli_dry_run(self):
        import subprocess, sys, tempfile as tf
        jobs = [{"brand": "avi", "platform": "twitter", "content": "hi https://a.io",
                 "scheduled_for": "2026-07-16T07:00:00", "content_id": "c",
                 "entry_point": "adhoc", "account_id": "a1"}]
        with tf.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(jobs, f); path = f.name
        env = {**os.environ, "SOCIAL_LOAD_FAKE_RAILS": "1"}
        out = subprocess.run([sys.executable, "tools/social_load.py", path],
                             capture_output=True, text=True, env=env,
                             cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        os.unlink(path)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("dry-run", out.stdout)
```

- [ ] **Step 2: Run to verify failure** — `python3 -m pytest tests/test_social_load.py::TestCli -v` — FAIL (no CLI yet; returncode non-zero or usage error).

- [ ] **Step 3: Implement**

```python
def _fake_rails_for_cli() -> Dict[str, Any]:
    """SOCIAL_LOAD_FAKE_RAILS=1: offline rails so dry-runs need no keys."""
    return {"zernio_list": lambda: [], "zernio_publish": lambda **kw: {"_id": "fake", "status": "scheduled"},
            "buffer_list": lambda cid: [], "buffer_draft": lambda *a: json.dumps([])}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="file-121 Pipe: the one social loader")
    ap.add_argument("jobs", help="JSON file: list of PostJob dicts")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--allow-stack", action="store_true")
    args = ap.parse_args()
    raw = json.load(open(args.jobs, encoding="utf-8"))
    jobs = [PostJob(**{k: v for k, v in j.items() if k in PostJob.__dataclass_fields__})
            for j in raw]
    rails = _fake_rails_for_cli() if os.getenv("SOCIAL_LOAD_FAKE_RAILS") == "1" else None
    results = load_jobs(jobs, commit=args.commit, allow_stack=args.allow_stack, rails=rails)
    bad = 0
    for r in results:
        j = r["job"]
        print(f"{j.brand:16} {j.platform:10} {r['action']:9} {r.get('detail')}")
        if r["action"] in ("error", "conflict"):
            bad += 1
    if not args.commit:
        print("DRY-RUN complete. Re-run with --commit to schedule.")
    return 4 if bad else 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    raise SystemExit(main())
```

Also add `import sys` to the module imports.

- [ ] **Step 4: Run the full suite** — `python3 -m pytest tests/test_social_load.py -v` — all pass.

- [ ] **Step 5: Commit**

```bash
git add -u tools tests && git commit -m "feat(social_load): CLI with offline fake-rails mode"
```

---

### Task 7: studio_publish.py delegates to the loader

**Files:**
- Modify: `tools/studio_publish.py:184-222` (the per-platform scheduling loop inside `main()`)
- Test: existing suite + a real dry-run

**Interfaces:**
- Consumes: `PostJob`, `load_jobs` from `tools.social_load`.
- Keeps: `parse_batch`, gate refusal, `BRAND_TO_PROFILE`, `STAGGER`, all CLI flags, the local per-batch ledger write (unchanged, appends as today).

- [ ] **Step 1: Replace the inner scheduling block**

In `main()`, add at the top of the file with the other imports:

```python
from tools.social_load import PostJob, load_jobs, canonical_brand
```

Replace lines 190-219 (the `for platform, content in b["posts"].items():` loop) with:

```python
            jobs, skips = [], []
            for platform, content in b["posts"].items():
                if plat_only and platform not in plat_only:
                    skips.append((platform, "not in --platforms channel policy")); continue
                if args.stagger:
                    hhmm = STAGGER.get(name.lower(), {}).get(platform)
                    if not hhmm:
                        skips.append((platform, "no stagger slot in file-103 grid")); continue
                    when_post = f"{stagger_date}T{hhmm}:00"
                else:
                    when_post = scheduled_for
                aid = acct_by_plat.get(platform)
                if not aid:
                    skips.append((platform, f"no {platform} account on this profile")); continue
                jobs.append(PostJob(
                    brand=canonical_brand(name), platform=platform, content=content,
                    scheduled_for=when_post, tz=args.tz,
                    media_urls=[media_url] if media_url else [],
                    content_id=os.path.splitext(os.path.basename(args.batch))[0],
                    entry_point="studio", account_id=aid))
            for platform, why in skips:
                print(f"    {platform:10} SKIP ({why})")
            for r in load_jobs(jobs, commit=args.commit, allow_stack=args.allow_stack):
                j, act = r["job"], r["action"]
                if act == "dry-run":
                    print(f"    {j.platform:10} OK  {len(j.content):>5} chars  -> {j.scheduled_for}")
                elif act == "scheduled":
                    res = r["detail"]
                    print(f"    {j.platform:10} SCHEDULED id={res.get('_id')} status={res.get('status')} @ {j.scheduled_for}")
                    ledger.append({"brand": name, "platform": j.platform,
                                   "post_id": res.get("_id"), "status": res.get("status"),
                                   "scheduled_for": j.scheduled_for})
                else:
                    print(f"    {j.platform:10} {act.upper()} {r.get('detail')}")
```

And add the new flag next to `--commit` in the argparser:

```python
    ap.add_argument("--allow-stack", action="store_true",
                    help="override the file-121 queue guard (deliberate same-day stack)")
```

- [ ] **Step 2: Run the unit suite (no regressions)**

Run: `python3 -m pytest tests/test_social_load.py -v` — all pass.

- [ ] **Step 3: Real dry-run against the shipped 118 batch (needs Zernio key)**

Run: `cd ~/paperclip && doppler run -p paperclip -c prd -- python3 tools/studio_publish.py ~/avo-telemetry/marketing_deliverables/118_aipg_week_2026-07-14/tue.md --when 2026-07-14 --stagger`
Expected: dry-run listing with `facebook`/`instagram` rows showing `OK ... -> 2026-07-14T06:30:00` / `T12:00:00`; NO schedule calls; exit 0.

- [ ] **Step 4: Verify the guard fires on a real collision (read-only, nothing scheduled)**

```bash
cd ~/paperclip && doppler run -p paperclip -c prd -- python3 - <<'EOF'
import sys, os
sys.path.insert(0, os.getcwd())
from tools.social_load import find_conflicts
from tools.zernio import list_zernio_posts
queue = list_zernio_posts()
hits = find_conflicts(queue, "facebook", "2026-07-14",
                      account_id="6a43fd0a9d9472faae32a6e6")
print("conflicts for AIPG FB 2026-07-14:", [h.get("_id") for h in hits])
assert hits, "expected the 118-batch 6:30a card to register as a conflict"
EOF
```

Expected: prints at least post id `6a540787311ca8eac12c05c8` (the 118 tue card) and the assert passes.

- [ ] **Step 5: Commit**

```bash
git add -u tools && git commit -m "refactor(studio_publish): schedule through social_load (guard + UTM + registry)"
```

---

### Task 8: Blog engine prompt routes through the loader

**Files:**
- Modify: `~/avo-telemetry/scripts/blog_engine_prompt.md` (step 8, line 27)

- [ ] **Step 1: Edit step 8**

In step 8, replace the sentence `Use ~/paperclip/tools/zernio.py (needs ZERNIO_API_KEY in env — run the engine via doppler run if the key is in Doppler).` with:

```
Distribute through THE LOADER, never raw zernio.py (file 121): write the pack as a jobs
JSON (fields: brand, platform, content, scheduled_for per the file-102 grid, content_id =
the blog slug, entry_point = "blog_engine", account_id from the brand's Zernio profile)
and run `python3 ~/paperclip/tools/social_load.py <jobs.json> --commit` under doppler.
The loader adds UTMs, refuses queue collisions (fix the time, do not --allow-stack without
a reason), writes the registry row, and hard-blocks WD until the rename. P&P packs use
brand "paperandpurpose" (Buffer DRAFTS; Miriam's approval stays the gate).
```

- [ ] **Step 2: Verify the edit**

Run: `grep -n "social_load" ~/avo-telemetry/scripts/blog_engine_prompt.md`
Expected: the new text present exactly once; `grep -c "tools/zernio.py" ...` returns 0 for step 8's distribute clause.

- [ ] **Step 3: Commit (avo-telemetry repo)**

```bash
cd ~/avo-telemetry && git add scripts/blog_engine_prompt.md && git commit -m "blog engine: distribute via social_load (file 121 Pipe)" && git pull --rebase --autostash && git push
```

---

### Task 9: Registry backfill from existing ledgers

**Files:**
- Create: `~/avo-telemetry/social_registry.jsonl` (via one-off script, not committed to paperclip)

- [ ] **Step 1: Backfill**

Run this one-off (plain python3, no keys needed):

```python
python3 - <<'EOF'
import json, pathlib
home = pathlib.Path.home()
import sys
sys.path.insert(0, str(home / "paperclip"))
from tools.social_load import append_registry

# 118 batch (AIPG cards, studio)
led = json.load(open(home / "avo-telemetry/marketing_deliverables/118_aipg_week_2026-07-14/studio_publish_ledger.json"))
for p in led["posts"]:
    append_registry({"brand": "aipg", "rail": "zernio", "platform": p["platform"],
                     "account_id": None, "post_id": p["post_id"],
                     "scheduled_for": p["scheduled_for"], "tz": p.get("tz"),
                     "content_id": p.get("batch", "118"), "utm_campaign": "aipg_118",
                     "entry_point": "studio", "media_url": None, "backfill": True})

# 116 video posts (adhoc)
led2 = json.load(open(home / "avo-telemetry/marketing_deliverables/116_video_leg_activation/zernio_schedule_ledger.json"))
for p in led2["posts"]:
    append_registry({"brand": p["brand"], "rail": "zernio", "platform": p["platform"],
                     "account_id": None, "post_id": p["post_id"],
                     "scheduled_for": p["scheduled_for"], "tz": "America/Chicago",
                     "content_id": "video_v1", "utm_campaign": f"{p['brand']}_video_v1",
                     "entry_point": "adhoc", "media_url": None, "backfill": True})
print(sum(1 for _ in open(home / "avo-telemetry/social_registry.jsonl")), "rows")
EOF
```

Expected output: `14 rows` (8 from the 118 ledger + 6 video posts).

- [ ] **Step 2: Commit registry to avo-telemetry**

```bash
cd ~/avo-telemetry && git add social_registry.jsonl && git commit -m "social registry: backfill from 116+118 ledgers (file 121)" && git push
```

---

### Task 10: Full verification + PR + merge

- [ ] **Step 1: Full unit suite**

Run: `cd ~/paperclip && python3 -m pytest tests/test_social_load.py -v`
Expected: all tests pass.

- [ ] **Step 2: Offline CLI end-to-end**

```bash
cd ~/paperclip && cat > /tmp/jobs_smoke.json <<'EOF'
[
 {"brand": "avi", "platform": "twitter", "content": "smoke https://automotiveintelligence.io/blog/x",
  "scheduled_for": "2026-07-16T07:00:00", "content_id": "smoke", "entry_point": "adhoc", "account_id": "a1"},
 {"brand": "wd", "platform": "facebook", "content": "smoke https://worshipdigital.co",
  "scheduled_for": "2026-07-16T09:00:00", "content_id": "smoke", "entry_point": "adhoc", "account_id": "a2"},
 {"brand": "paperandpurpose", "platform": "instagram", "content": "smoke https://paperandpurpose.co",
  "scheduled_for": "2026-07-16T12:00:00", "content_id": "smoke", "entry_point": "adhoc",
  "business_key": "paperandpurpose"}
]
EOF
SOCIAL_LOAD_FAKE_RAILS=1 python3 tools/social_load.py /tmp/jobs_smoke.json
```

Expected: three lines with actions `dry-run` (avi), `blocked` (wd, file-120 message), `dry-run` (paperandpurpose buffer); exit 0. `blocked` is intentionally not a failure exit: blocks are policy outcomes, not errors.

- [ ] **Step 3: Live dry-run of studio_publish on 118 tue.md** (repeat Task 7 Step 3 to confirm nothing regressed after all commits).

- [ ] **Step 4: PR + merge (merge authority is standing)**

```bash
cd ~/paperclip && git push -u origin feat/social-load-pipe && gh pr create --fill && gh pr merge --squash --delete-branch
```

- [ ] **Step 5: Update cmo_state.md** — one decision line: Pipe live, loader is the only sanctioned scheduling path, registry seeded, blog engine re-routed; flag Growth & Analytics that Phase 3 can start once GSC platform-property OAuth lands.
