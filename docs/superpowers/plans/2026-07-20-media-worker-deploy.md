# Media Worker Deploy Build Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Make the video worker image self-sufficient and deployable: bake static assets at the paths the scripts expect, boot-pull the model + stock from Blob, and expose a `/admin/run-video` trigger that renders and stages (never schedules).

**Architecture:** Three tasks on top of the merged `tools/media_worker/` + `services/media_worker.py` + `Dockerfile.media-worker`. D1 bakes fonts + brand logos at the HOME-relative paths the ported scripts hardcode (option A: zero script rewrites). D2 adds a boot-pull that fetches the whisper model + gated stock from Vercel Blob into the HOME/cache layout, dedup-guarded. D3 wires the trigger endpoint and the three deferred stage steps (Blob push, REVIEW_LOG, CMO flag).

**Tech Stack:** Python 3.11, pytest, ffmpeg, whisper.cpp (static), Vercel Blob, FastAPI (paperclip app.py), Railway.

## Global Constraints
- Python 3.11; tests are pytest in `tests/`; interpreter on the dev Mac is `python3` (not `python`).
- No em-dashes in any authored comment/string/doc.
- STAGE-AND-FLAG ONLY: nothing in this build schedules or publishes outward. `run_video` stages a master + review sheet and raises a CMO flag; a human gates before scheduling.
- The ported scripts read HOME-relative asset paths and NO env vars: `cut_talking_head.py`/`build_short.py` use `FONTS = HOME/avo-telemetry/assets/fonts` and logos at `HOME/<brand>-site/public/...` + `HOME/avo-telemetry/assets/brand/...`; `build_short.py` reads stock at `HOME/stock_library/`. The container runs as root so `HOME=/root`. Stage assets there.
- Blob gotchas: `vercel blob list/put` print to STDERR; random filename suffixes despite `--add-random-suffix false` -> dedup against a sha256 manifest (reuse `tools/media_worker/blob_sync.py`).
- Test isolation: any commit-path test must never write to the real social_registry, real Blob, or real state files. Inject runners / monkeypatch / use tmp_path. New test files under `tests/media_worker/` are un-ignored by the existing `.gitignore` negation; verify `git ls-files` after committing.
- The worker image targets linux/amd64 (Railway). Local builds use `--platform linux/amd64`.

## File Structure
- `assets/home_stage/...` (CREATE) — fonts + brand logos mirroring the exact HOME-relative layout, `COPY`d to `/root/` in one shot.
- `Dockerfile.media-worker` (MODIFY) — replace the `/opt/media/fonts` font bake with `COPY assets/home_stage/ /root/`; keep everything else.
- `tools/media_worker/smoke.py` (MODIFY) — check the font at its new HOME path.
- `tools/media_worker/boot.py` (CREATE) — Blob boot-pull into the HOME/cache layout, dedup-guarded, injectable runner.
- `docker-entrypoint.sh` (CREATE) — run boot-pull then the process.
- `services/media_worker.py` (MODIFY) — implement the three deferred stage steps behind small, testable helpers.
- `app.py` (MODIFY) — add `POST /admin/run-video`.
- Tests: `tests/media_worker/test_boot.py`, `tests/media_worker/test_stage_steps.py`, `tests/media_worker/test_run_video_endpoint.py` (CREATE).

---

### Task D1: Self-sufficient image (bake fonts + brand logos at HOME paths)

**Files:**
- Create: `assets/home_stage/avo-telemetry/assets/fonts/{InterTight,Archivo,Montserrat,SpaceGrotesk}.ttf`, `assets/home_stage/avo-telemetry/assets/brand/{bae_lockup_cyan,bae_crown_cyan,bookd_logo_cyan}.png`, `assets/home_stage/ai-phone-guy-site/public/aipg-logo.png`, `assets/home_stage/worship-digital-site/public/WD-Logo.png`, `assets/home_stage/automotive-intelligence-site/public/{logo,logo-mark}.png`
- Modify: `Dockerfile.media-worker`, `tools/media_worker/smoke.py`

- [ ] **Step 1: Stage the assets into the build context** (mirror the HOME layout)

```bash
cd <worktree>
mkdir -p assets/home_stage/avo-telemetry/assets/fonts assets/home_stage/avo-telemetry/assets/brand \
         assets/home_stage/ai-phone-guy-site/public assets/home_stage/worship-digital-site/public \
         assets/home_stage/automotive-intelligence-site/public
cp assets/fonts/*.ttf assets/home_stage/avo-telemetry/assets/fonts/
cp ~/avo-telemetry/assets/brand/bae_lockup_cyan.png ~/avo-telemetry/assets/brand/bae_crown_cyan.png ~/avo-telemetry/assets/brand/bookd_logo_cyan.png assets/home_stage/avo-telemetry/assets/brand/
cp ~/ai-phone-guy-site/public/aipg-logo.png assets/home_stage/ai-phone-guy-site/public/
cp ~/worship-digital-site/public/WD-Logo.png assets/home_stage/worship-digital-site/public/
cp ~/automotive-intelligence-site/public/logo.png ~/automotive-intelligence-site/public/logo-mark.png assets/home_stage/automotive-intelligence-site/public/
```

- [ ] **Step 2: Dockerfile — bake the HOME layout.** Replace `COPY assets/fonts/ /opt/media/fonts/` with:

```dockerfile
# static assets at the HOME-relative paths the ported scripts hardcode (HOME=/root)
COPY assets/home_stage/ /root/
```

Keep the `ENV` block but drop the now-unused `FONTS_DIR=/opt/media/fonts` line (leave STOCK_LIB/WHISPER_MODEL/etc.).

- [ ] **Step 3: smoke.py — check the font at its baked HOME path.** Change the `ImageFont.truetype("/opt/media/fonts/InterTight.ttf", 78)` line to `ImageFont.truetype("/root/avo-telemetry/assets/fonts/InterTight.ttf", 78)`.

- [ ] **Step 4: Build + verify self-sufficiency.** (Controller does this.)

Run: `docker build --platform linux/amd64 -f Dockerfile.media-worker -t avo-media-worker .`
Then a render with ONLY the model + a take mounted (NO font/logo mounts):
`docker run --rm --platform linux/amd64 -v <model>:/opt/media/cache/whisper/ggml-small.en.bin:ro -v <proofdir>:/work avo-media-worker sh -c "PYTHONPATH=/app python /work/driver.py"`
Expected: valid 1080x1920 master + sheet produced WITHOUT any font/logo mount (proves the image is self-sufficient for static assets).

- [ ] **Step 5: Commit** `assets/home_stage/`, Dockerfile, smoke.py. Verify staged assets tracked (`git ls-files assets/home_stage | wc -l` == 10). Message: `build(media-worker): bake fonts + brand logos at HOME paths (self-sufficient render)`.

---

### Task D2: Boot-pull (model + gated stock from Blob into the HOME/cache layout)

Pull the large/dynamic assets at container start. Model -> `WHISPER_MODEL` path; gated stock -> `HOME/stock_library` (where `build_short.py` reads it). Dedup against a sha256 manifest so a restart does not re-download 487MB. The Blob list+fetch goes through an injected runner so tests stay offline.

**Files:**
- Create: `tools/media_worker/boot.py`, `docker-entrypoint.sh`
- Test: `tests/media_worker/test_boot.py`
- Modify: `Dockerfile.media-worker` (ENTRYPOINT)

**Interfaces:**
- Produces: `plan_pull(remote_entries: list[dict], manifest: dict, dest_root: str) -> list[dict]` (pure: which objects to download, where; skip when the manifest sha matches). Each remote entry is `{"pathname": str, "url": str, "size": int, "sha": str|None}`. `boot_pull(prefixes: list[str], dest_root: str, manifest_path: str, lister, fetcher) -> dict` orchestrates via injected `lister(prefix)->list[dict]` and `fetcher(url, dest)->str(sha)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/media_worker/test_boot.py
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tools.media_worker.boot import plan_pull, boot_pull


def test_plan_pull_skips_matching_sha_downloads_new(tmp_path):
    remote = [{"pathname": "a.bin", "url": "u/a", "size": 3, "sha": "sha-a"},
              {"pathname": "b.bin", "url": "u/b", "size": 3, "sha": "sha-b"}]
    manifest = {"a.bin": "sha-a"}  # a already local, b new
    plan = plan_pull(remote, manifest, str(tmp_path))
    assert [p["pathname"] for p in plan] == ["b.bin"]
    assert plan[0]["dest"] == os.path.join(str(tmp_path), "b.bin")


def test_boot_pull_downloads_only_new_and_updates_manifest(tmp_path):
    mp = str(tmp_path / "m.json")
    fetched = []
    def lister(prefix):
        return [{"pathname": f"{prefix}x.bin", "url": "u/x", "size": 1, "sha": "shax"}]
    def fetcher(url, dest):
        os.makedirs(os.path.dirname(dest), exist_ok=True); open(dest, "w").write("x"); fetched.append(dest); return "shax"
    m = boot_pull(["p/"], str(tmp_path), mp, lister=lister, fetcher=fetcher)
    assert len(fetched) == 1 and m["p/x.bin"] == "shax"
    fetched.clear()
    boot_pull(["p/"], str(tmp_path), mp, lister=lister, fetcher=fetcher)  # 2nd run: nothing new
    assert fetched == []
```

- [ ] **Step 2: Run test to verify it fails** — `cd <wt> && python3 -m pytest tests/media_worker/test_boot.py -v` (ModuleNotFoundError).

- [ ] **Step 3: Implement `tools/media_worker/boot.py`**

```python
# tools/media_worker/boot.py
"""Boot-pull large/dynamic assets from Vercel Blob into the HOME/cache layout.

Model + gated stock are NOT baked into the image; they are fetched at container
start and deduped against a sha256 manifest so a restart does not re-download the
487MB model. The Blob list + fetch go through injected callables so tests stay
offline. Blob list prints to STDERR and appends random filename suffixes; the
real lister must strip suffixes back to the logical pathname."""
from __future__ import annotations
import json, os
from typing import Callable, Dict, List, Optional


def load_manifest(path: str) -> Dict[str, str]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_manifest(path: str, m: Dict[str, str]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=2, sort_keys=True)


def plan_pull(remote_entries: List[dict], manifest: Dict[str, str], dest_root: str) -> List[dict]:
    """Entries whose sha differs from (or is absent in) the manifest, with a dest path."""
    out = []
    for e in remote_entries:
        name = e["pathname"]
        if manifest.get(name) != e.get("sha"):
            out.append({**e, "dest": os.path.join(dest_root, name)})
    return out


def boot_pull(prefixes: List[str], dest_root: str, manifest_path: str,
              lister: Callable[[str], List[dict]], fetcher: Callable[[str, str], str]) -> Dict[str, str]:
    """List each prefix, download only changed objects, update the manifest.
    Records a pathname ONLY after a successful fetch (a failed download is retried
    next boot, never marked present)."""
    manifest = load_manifest(manifest_path)
    remote: List[dict] = []
    for p in prefixes:
        remote.extend(lister(p))
    todo = plan_pull(remote, manifest, dest_root)
    try:
        for item in todo:
            sha = fetcher(item["url"], item["dest"])
            manifest[item["pathname"]] = sha or item.get("sha")
    finally:
        save_manifest(manifest_path, manifest)
    return manifest
```

- [ ] **Step 4: Run tests to verify they pass** — `python3 -m pytest tests/media_worker/test_boot.py -v` (2 passed).

- [ ] **Step 5: Entrypoint.** Create `docker-entrypoint.sh`:

```bash
#!/bin/sh
set -e
# boot-pull model + gated stock from Blob into the HOME/cache layout (idempotent, deduped)
python -m tools.media_worker.boot || echo "boot-pull skipped or partial (see logs)"
exec "$@"
```

In the Dockerfile add (before CMD): `COPY docker-entrypoint.sh /usr/local/bin/` + `RUN chmod +x /usr/local/bin/docker-entrypoint.sh` + `ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]`. Add a `__main__` block to `boot.py` that builds the real lister/fetcher (vercel blob list by prefix stripping random suffixes; fetch via the object URL with the token) and calls `boot_pull` for the model + stock prefixes. The real lister/fetcher are exercised at deploy, not in unit tests.

- [ ] **Step 6: Commit** boot.py, test, entrypoint, Dockerfile. Verify test tracked. Message: `feat(media-worker): boot-pull model + stock from Blob (dedup, HOME layout)`.

---

### Task D3: `/admin/run-video` trigger + wire the three deferred stage steps

Implement the deferred steps in `run_video` behind small helpers, and expose the endpoint. Stage-and-flag only.

**Files:**
- Modify: `services/media_worker.py`, `app.py`
- Test: `tests/media_worker/test_stage_steps.py`, `tests/media_worker/test_run_video_endpoint.py`

**Interfaces:**
- Produces: `stage_to_blob(paths: dict, upload) -> dict` (push master+sheet via injected `upload`, return blob keys); `append_review_log(entry: str, log_path: str) -> None`; `write_cmo_flag(text: str, state_path: str) -> None`. `run_video(take, edit)` calls `render_one` then these, returning `{"status":"staged", "master":..., "sheet":..., "flag":True}`.

- [ ] **Step 1: Write the failing tests** (stage steps)

```python
# tests/media_worker/test_stage_steps.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from services.media_worker import append_review_log, write_cmo_flag, stage_to_blob


def test_append_review_log_appends(tmp_path):
    p = tmp_path / "REVIEW_LOG.md"; p.write_text("head\n")
    append_review_log("2026-07-20 rendered clip6s", str(p))
    body = p.read_text()
    assert "head" in body and "2026-07-20 rendered clip6s" in body


def test_write_cmo_flag_appends_flag(tmp_path):
    p = tmp_path / "cmo_state.md"; p.write_text("# CMO\n")
    write_cmo_flag("VIDEO staged: clip6s master + sheet on Blob, awaiting file-133 gate", str(p))
    assert "VIDEO staged" in p.read_text()


def test_stage_to_blob_uploads_master_and_sheet():
    calls = []
    def fake_upload(files, root, manifest_path, runner=None):
        calls.extend(files); return {}
    res = stage_to_blob({"master": "/o/m.mp4", "sheet": "/o/s.png"}, fake_upload)
    assert "/o/m.mp4" in calls and "/o/s.png" in calls
```

- [ ] **Step 2: Run to verify fail** — `python3 -m pytest tests/media_worker/test_stage_steps.py -v`.

- [ ] **Step 3: Implement the helpers in `services/media_worker.py`** (append after `run_video`; keep no em-dashes)

```python
import os
from datetime import datetime, timezone


def append_review_log(entry: str, log_path: str) -> None:
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"\n- {stamp} {entry}\n")


def write_cmo_flag(text: str, state_path: str) -> None:
    """Append a stage-and-flag entry to the CMO state file. Never schedules."""
    with open(state_path, "a", encoding="utf-8") as f:
        f.write(f"\n VIDEO WORKER FLAG: {text}\n")


def stage_to_blob(paths: dict, upload) -> dict:
    """Push the master + sheet to Blob via the injected upload() (blob_sync.upload)."""
    files = [paths[k] for k in ("master", "sheet") if paths.get(k)]
    root = os.path.dirname(files[0]) if files else "."
    manifest = os.path.join(root, ".blob_manifest.json")
    upload(files, root, manifest)
    return {"blob_pushed": files}
```

Then update `run_video` to call `render_one`, then (guarded by env / injected deps so unit tests do not fire real Blob) `stage_to_blob` + `append_review_log` + `write_cmo_flag`, returning `{"status":"staged", ...}`. Keep the default path safe: if `BLOB_READ_WRITE_TOKEN` is unset, skip the Blob push and still stage locally + flag (documented).

- [ ] **Step 4: Add the endpoint to `app.py`.** Mirror `POST /admin/run-watchdog`. Read the existing watchdog route first and match its auth/shape. Body: `{take, edit}` -> `run_video(take, edit)` -> JSON. Add `tests/media_worker/test_run_video_endpoint.py` using FastAPI `TestClient` with `run_video` monkeypatched to a stub (no real render). Assert 200 + `status == "staged"`.

- [ ] **Step 5: Run all tests** — `python3 -m pytest tests/media_worker -q`. Commit. Verify test files tracked. Message: `feat(media-worker): /admin/run-video trigger + wire Blob/REVIEW_LOG/CMO stage steps`.

---

## Self-Review
- Coverage: asset self-sufficiency (D1), boot-pull (D2), trigger + stage steps (D3) cover the runbook's [BUILD] items. Railway service creation + laptop-off remain owner steps (out of scope, flagged).
- Placeholders: the real Blob lister/fetcher in boot.py `__main__` and the app.py route match existing patterns (watchdog); implementers read those first.
- Types: `plan_pull`/`boot_pull` (D2), `stage_to_blob`/`append_review_log`/`write_cmo_flag` (D3) consumed by `run_video`; signatures fixed above.
