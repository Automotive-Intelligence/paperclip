# Video Cloud-Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **GATE:** Tasks 6-8 (image, service wiring, deploy) are BLOCKED until the Team Principal rules on the 3 decisions in `marketing_deliverables/142_cutting_room_video_worker_audit_and_plan.md` §10. Tasks 1-5 are architecture-independent pure logic + tests and could run first, but per the charter ("do NOT build until the plan is agreed") NOTHING starts until the TP gives the go. This plan exists so execution is immediate on that go.

**Goal:** Render an AIPG-class founder short end-to-end on a Railway worker (transcribe, cut, b-roll, caption, render, gate, stage) with Michael's Mac closed.

**Architecture:** Containerize the existing local pipeline unchanged; do not rewrite it. Thin, testable Python glue in `tools/media_worker/` wraps the proven `scripts/cut_talking_head.py` + `scripts/video_review_sheet.py`, driven by a per-take `edit.json` intake. Assets (fonts, brand logos, gated stock, the 487MB whisper model) are pulled from Vercel Blob into a cache at boot; masters + contact-sheet go back to Blob; a CMO flag stages the file-133 human gate. The worker runs as a Railway service (Dockerfile), NEVER `railway run` from the Mac.

**Tech Stack:** Python 3.11, pytest 9.1.1, ffmpeg (libx264/loudnorm/zoompan/overlay), whisper.cpp (`whisper-cli`, ggml-small.en.bin), Pillow (variable fonts), Vercel Blob, Railway, Doppler.

## Global Constraints

- Python **3.11** (`paperclip/.python-version`); tests are pytest, live in `paperclip/tests/`.
- **No em-dashes** in any authored copy, comment, or caption (brand law, all brands).
- Talking-head path uses Michael's **REAL take audio only**, loudnorm -16 LUFS. The ElevenLabs clone (`build_short.py`) is a separate product, never the founder VO.
- **File-133 + file-117 gates with visible receipts** (contact sheet + REVIEW_LOG) before anything ships; **CMO go before scheduling**; **stage-and-flag, never silent auto-fire** for anything outward-facing.
- **Book'd = Ryan's signature always**; Book'd masters never auto-post (route to Ryan).
- **No fabricated stats** (only in-take-sourced claims).
- **Config-driven, zero Mac hardcoding**: every root path is env-overridable (`STOCK_LIB`, `FONTS_DIR`, `BRAND_ASSETS`, `WHISPER_MODEL`, `RENDERS_OUT`); defaults may match the Mac layout but must be overridable.
- **Blob gotcha**: `vercel blob list/put` print to STDERR (capture `2>&1`) and append random filename suffixes despite `--add-random-suffix false`; dedupe against a local manifest (path + sha256), never a naive re-sync.
- **Zernio quirks** the publish path must preserve: media lives in `mediaItems` not `media`; a failed post's status stays `failed` after edit (recreate + delete, never blind-retry); a timeout means "may have published" (a human checks the platform before any retry).
- The worker must **genuinely run on Railway** (a service/job), not be `railway run` from the Mac. Cloud secrets are not cloud execution.

## File Structure

- `tools/social_load.py` (MODIFY) — add the Twitter 280 guard at the single loader chokepoint.
- `tools/media_worker/__init__.py` (CREATE) — package marker.
- `tools/media_worker/edit_spec.py` (CREATE) — map `edit.json` to `cut_talking_head.py` argv. Pure, no I/O.
- `tools/media_worker/transcribe.py` (CREATE) — ffmpeg wav extract + `whisper-cli` argv + `transcribe()`.
- `tools/media_worker/blob_sync.py` (CREATE) — sha256 manifest + dedup upload/download planning. Pure planning + injected runner.
- `tools/media_worker/render.py` (CREATE) — orchestrate transcribe -> cut -> contact-sheet for one take.
- `tools/media_worker/smoke.py` (CREATE) — in-image self-check (ffmpeg, whisper-cli, variable font).
- `services/media_worker.py` (CREATE) — the trigger handler: pull assets, render, push, stage CMO flag.
- `Dockerfile.media-worker` (CREATE) — the worker image.
- `tests/test_social_load_twitter_limit.py`, `tests/media_worker/test_edit_spec.py`, `tests/media_worker/test_transcribe.py`, `tests/media_worker/test_blob_sync.py`, `tests/media_worker/test_render_smoke.py` (CREATE).

All new code lives in the **paperclip** repo (shares `tools/zernio.py`, `tools/social_load.py`, `tools/studio_publish.py`). Work in a git worktree (other B&T crews are in paperclip); branch, PR, TP verifies + merges.

---

### Task 0: Worktree + plan landing (prerequisite)

**Files:**
- Create: `paperclip/docs/superpowers/plans/2026-07-19-video-cloud-worker.md` (copy of this file)

- [ ] **Step 1: Create the worktree** (via superpowers:using-git-worktrees) off `paperclip` `main` (currently `c81c707`), branch `feat/media-worker`.
- [ ] **Step 2: Copy this plan into the repo**

```bash
mkdir -p docs/superpowers/plans
cp ~/avo-telemetry/docs/superpowers/plans/2026-07-19-video-cloud-worker.md docs/superpowers/plans/
git add docs/superpowers/plans/2026-07-19-video-cloud-worker.md
git commit -m "docs: land video cloud-worker plan"
```

---

### Task 1: Twitter 280-char guard in the loader (confirmed bug fix)

Architecture-independent; standalone-PR-worthy. Fixes the confirmed bug at `studio_publish.py:222` (reports char count, never validates). The guard belongs in `load_jobs()` because every scheduled post rides through it, and it must run AFTER `tag_links()` (UTM tagging is what changes the string), counting URLs as 23 (Twitter wraps every link to a fixed-width t.co URL, so UTM params do not lengthen the tweet).

**Files:**
- Modify: `tools/social_load.py` (add `tweet_length`, insert guard in `load_jobs` after line 207; count `too_long` as bad in `main`)
- Test: `tests/test_social_load_twitter_limit.py`

**Interfaces:**
- Produces: `tweet_length(text: str) -> int`; `load_jobs(...)` returns a result dict with `action == "too_long"` for over-limit twitter jobs (skipped, not scheduled).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_social_load_twitter_limit.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.social_load import tweet_length, load_jobs, PostJob

_FAKE = {"zernio_list": lambda: [],
         "zernio_publish": lambda **k: {"_id": "x", "status": "scheduled"},
         "buffer_list": lambda c: [], "buffer_draft": lambda *a: "[]"}
_CFG = {"wd_rename_done": True}


def _job(content, platform="twitter"):
    return PostJob(brand="aipg", platform=platform, content=content,
                   scheduled_for="2026-07-25T07:15:00", content_id="t", entry_point="studio")


def test_tweet_length_counts_each_url_as_23():
    url = "https://example.com/x?utm_campaign=aipg_t&utm_source=twitter&utm_medium=social"
    assert len(url) > 23
    assert tweet_length(f"See {url} now") == len("See ") + 23 + len(" now")


def test_over_280_twitter_is_flagged_not_scheduled():
    res = load_jobs([_job("a" * 300)], commit=True, rails=_FAKE, cfg=_CFG)
    assert res[0]["action"] == "too_long"


def test_under_280_twitter_is_scheduled():
    res = load_jobs([_job("short and sweet")], commit=True, rails=_FAKE, cfg=_CFG)
    assert res[0]["action"] == "scheduled"


def test_long_content_on_non_twitter_is_unaffected():
    res = load_jobs([_job("a" * 300, platform="linkedin")], commit=True, rails=_FAKE, cfg=_CFG)
    assert res[0]["action"] == "scheduled"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/paperclip && python -m pytest tests/test_social_load_twitter_limit.py -v`
Expected: FAIL (ImportError: cannot import name `tweet_length`).

- [ ] **Step 3: Add `tweet_length` to `tools/social_load.py`** (after the `_URL_RE` definition, near line 27)

```python
_TCO_LEN = 23  # Twitter wraps every URL to a fixed-width t.co link


def tweet_length(text: str) -> int:
    """Twitter weighted length for English brand copy: each URL counts as 23
    (t.co), so UTM params do not lengthen the tweet. Code-point length is exact
    for Latin copy; CJK weighting is not needed for our brands."""
    return len(_URL_RE.sub("x" * _TCO_LEN, text))
```

- [ ] **Step 4: Insert the guard in `load_jobs`** immediately after `content = tag_links(...)` (currently line 207-208), before `day = ...`

```python
        if job.platform == "twitter" and tweet_length(content) > 280:
            results.append({"job": job, "action": "too_long",
                            "detail": f"twitter post is {tweet_length(content)} chars (>280) after UTM tagging"})
            continue
```

- [ ] **Step 5: Count `too_long` as bad in `main`** (modify the condition at line 286)

```python
        if res["action"] in ("error", "conflict", "too_long"):
            bad += 1
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd ~/paperclip && python -m pytest tests/test_social_load_twitter_limit.py -v`
Expected: 4 passed.

- [ ] **Step 7: Regression-check the existing loader tests**

Run: `cd ~/paperclip && python -m pytest tests/test_social_load.py tests/test_social_load_service.py -v`
Expected: all pass (no regressions).

- [ ] **Step 8: Commit**

```bash
git add tools/social_load.py tests/test_social_load_twitter_limit.py
git commit -m "fix(social-load): reject twitter posts over 280 chars (t.co-weighted)"
```

---

### Task 2: edit.json -> cut_talking_head argv (the intake contract)

Pure function, no I/O. Maps a per-take `edit.json` onto the exact CLI of `scripts/cut_talking_head.py` (`<brand> <take> <words> <out> [--hook] [--cut A-B]... [--corrections k=v;k=v] [--broll-at "start|clip|in|dur"]...`). Auto-flub-detection / auto-b-roll are OUT of scope (charter: port what exists).

**Files:**
- Create: `tools/media_worker/__init__.py` (empty), `tools/media_worker/edit_spec.py`
- Test: `tests/media_worker/test_edit_spec.py` (+ create `tests/media_worker/__init__.py` empty)

**Interfaces:**
- Produces: `build_cut_argv(edit: dict, take: str, words: str, out: str, script: str = "scripts/cut_talking_head.py") -> list[str]`. `edit` keys: `brand` (required), `hook` (str|None), `cuts` (list[str] like `"61.2-68.4"`), `corrections` (str like `"tick=ticket;invoca=Invoca"`), `broll_at` (list[str] like `"0|/path/clip.mp4|1.0|4.0"`).

- [ ] **Step 1: Write the failing test**

```python
# tests/media_worker/test_edit_spec.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import pytest
from tools.media_worker.edit_spec import build_cut_argv


def test_minimal_edit_positional_args():
    argv = build_cut_argv({"brand": "aipg"}, "take.mp4", "w.json", "out.mp4")
    assert argv[:5] == ["python3", "scripts/cut_talking_head.py", "aipg", "take.mp4", "w.json"]
    assert argv[5] == "out.mp4"
    assert "--hook" not in argv


def test_full_edit_maps_every_flag():
    edit = {"brand": "aipg", "hook": "ON SCREEN HOOK",
            "cuts": ["61.2-68.4", "70-72.5"], "corrections": "tick=ticket",
            "broll_at": ["0|/s/a.mp4|1.0|4.0", "4.0|/s/b.mp4|0|3.5"]}
    argv = build_cut_argv(edit, "t.mp4", "w.json", "o.mp4")
    assert argv.count("--cut") == 2 and "61.2-68.4" in argv and "70-72.5" in argv
    assert argv.count("--broll-at") == 2 and "0|/s/a.mp4|1.0|4.0" in argv
    assert argv[argv.index("--hook") + 1] == "ON SCREEN HOOK"
    assert argv[argv.index("--corrections") + 1] == "tick=ticket"


def test_missing_brand_raises():
    with pytest.raises(ValueError):
        build_cut_argv({}, "t.mp4", "w.json", "o.mp4")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/paperclip && python -m pytest tests/media_worker/test_edit_spec.py -v`
Expected: FAIL (ModuleNotFoundError: tools.media_worker.edit_spec).

- [ ] **Step 3: Write minimal implementation**

```python
# tools/media_worker/edit_spec.py
"""Map a per-take edit.json onto scripts/cut_talking_head.py argv. Pure; no I/O.

edit.json faithfully captures the decisions an operator used to type as CLI flags.
No new editorial intelligence lives here (charter: port what exists)."""
from __future__ import annotations
from typing import List, Optional


def build_cut_argv(edit: dict, take: str, words: str, out: str,
                   script: str = "scripts/cut_talking_head.py") -> List[str]:
    brand = edit.get("brand")
    if not brand:
        raise ValueError("edit.json missing required 'brand'")
    argv: List[str] = ["python3", script, brand, take, words, out]
    hook: Optional[str] = edit.get("hook")
    if hook:
        argv += ["--hook", hook]
    for span in edit.get("cuts", []) or []:
        argv += ["--cut", span]
    corrections = edit.get("corrections")
    if corrections:
        argv += ["--corrections", corrections]
    for spec in edit.get("broll_at", []) or []:
        argv += ["--broll-at", spec]
    return argv
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/paperclip && python -m pytest tests/media_worker/test_edit_spec.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/media_worker/__init__.py tools/media_worker/edit_spec.py tests/media_worker/__init__.py tests/media_worker/test_edit_spec.py
git commit -m "feat(media-worker): edit.json -> cut_talking_head argv mapping"
```

---

### Task 3: whisper transcribe wrapper (formalize the manual step)

The transcribe step exists only in comments today. Formalize it: extract 16k mono WAV, run `whisper-cli -ml 1 -sow -oj`, return the words JSON in the schema `cut_talking_head.load_words` consumes (`transcription[i].text`, `.offsets.from`, `.offsets.to` in ms). Argv builders are pure and unit-tested; the end-to-end `transcribe()` is integration-tested behind a binary-presence skip (present in the image).

**Files:**
- Create: `tools/media_worker/transcribe.py`
- Test: `tests/media_worker/test_transcribe.py`

**Interfaces:**
- Produces: `wav_extract_argv(take: str, wav: str) -> list[str]`; `whisper_argv(model: str, wav: str, out_base: str) -> list[str]`; `transcribe(take: str, model: str, workdir: str) -> str` (path to `<name>.json`).

- [ ] **Step 1: Write the failing test**

```python
# tests/media_worker/test_transcribe.py
import os, shutil, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import pytest
from tools.media_worker.transcribe import wav_extract_argv, whisper_argv, transcribe


def test_wav_extract_argv_is_16k_mono_pcm():
    a = wav_extract_argv("take.mp4", "take.16k.wav")
    assert a[0] == "ffmpeg" and "-ar" in a and a[a.index("-ar") + 1] == "16000"
    assert a[a.index("-ac") + 1] == "1" and "pcm_s16le" in a and a[-1] == "take.16k.wav"


def test_whisper_argv_has_word_timestamp_flags():
    a = whisper_argv("m.bin", "take.16k.wav", "out")
    for flag in ("-m", "-ml", "-sow", "-oj", "-f", "-of"):
        assert flag in a, flag
    assert a[a.index("-ml") + 1] == "1"
    assert a[a.index("-of") + 1] == "out"


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("whisper-cli")),
                    reason="ffmpeg + whisper-cli required (present in the worker image)")
def test_transcribe_emits_consumable_schema(tmp_path):
    model = os.environ.get("WHISPER_MODEL",
                           os.path.expanduser("~/stock_library/.whisper_models/ggml-small.en.bin"))
    if not os.path.exists(model):
        pytest.skip("whisper model not present locally")
    # 1s tone as a stand-in take; asserts schema shape, not transcript accuracy.
    take = tmp_path / "tone.wav"
    os.system(f'ffmpeg -v error -y -f lavfi -i "sine=frequency=440:duration=1" -ar 44100 "{take}"')
    out = transcribe(str(take), model, str(tmp_path))
    d = json.load(open(out))
    assert "transcription" in d
    for seg in d["transcription"]:
        assert "text" in seg and "offsets" in seg
        assert "from" in seg["offsets"] and "to" in seg["offsets"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/paperclip && python -m pytest tests/media_worker/test_transcribe.py -v`
Expected: the two argv tests FAIL (ModuleNotFoundError); the integration test skips or fails.

- [ ] **Step 3: Write implementation**

```python
# tools/media_worker/transcribe.py
"""Formalize the transcribe step (was manual, documented only in comments).

take.mp4 -> 16k mono WAV -> whisper-cli -ml 1 -sow -oj -> <name>.json, in the
schema scripts/cut_talking_head.py:load_words() consumes."""
from __future__ import annotations
import os, pathlib, subprocess
from typing import List


def wav_extract_argv(take: str, wav: str) -> List[str]:
    return ["ffmpeg", "-v", "error", "-y", "-i", take,
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav]


def whisper_argv(model: str, wav: str, out_base: str, binary: str = "whisper-cli") -> List[str]:
    # -ml 1 -sow: one word per segment with real timestamps. -oj: JSON output at <out_base>.json.
    return [binary, "-m", model, "-ml", "1", "-sow", "-oj", "-f", wav, "-of", out_base]


def transcribe(take: str, model: str, workdir: str) -> str:
    os.makedirs(workdir, exist_ok=True)
    name = pathlib.Path(take).stem
    wav = os.path.join(workdir, f"{name}.16k.wav")
    out_base = os.path.join(workdir, name)
    subprocess.run(wav_extract_argv(take, wav), check=True)
    subprocess.run(whisper_argv(model, wav, out_base), check=True)
    return f"{out_base}.json"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/paperclip && python -m pytest tests/media_worker/test_transcribe.py -v`
Expected: 2 passed, 1 passed-or-skipped (integration).

- [ ] **Step 5: Commit**

```bash
git add tools/media_worker/transcribe.py tests/media_worker/test_transcribe.py
git commit -m "feat(media-worker): formalize whisper transcribe step (16k wav + -ml 1 -sow -oj)"
```

---

### Task 4: Blob sync + dedup manifest

The scheduled/boot sync MUST dedupe against a local manifest (path + sha256), because Blob appends random suffixes despite `--add-random-suffix false` (a naive re-run duplicates). Planning logic is pure and unit-tested; the actual `vercel blob` calls go through an injected runner so tests never touch the network. STDERR capture is honored in the runner.

**Files:**
- Create: `tools/media_worker/blob_sync.py`
- Test: `tests/media_worker/test_blob_sync.py`

**Interfaces:**
- Produces: `sha256_file(path: str) -> str`; `load_manifest(path: str) -> dict`; `save_manifest(path: str, m: dict) -> None`; `plan_uploads(files: list[str], root: str, manifest: dict) -> list[str]` (returns only paths whose sha changed vs manifest).

- [ ] **Step 1: Write the failing test**

```python
# tests/media_worker/test_blob_sync.py
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tools.media_worker.blob_sync import sha256_file, load_manifest, save_manifest, plan_uploads


def test_sha256_stable(tmp_path):
    p = tmp_path / "a.txt"; p.write_text("hello")
    assert sha256_file(str(p)) == sha256_file(str(p))


def test_plan_uploads_skips_unchanged_and_flags_changed(tmp_path):
    root = tmp_path
    (root / "x.txt").write_text("one")
    (root / "y.txt").write_text("two")
    manifest = {"x.txt": sha256_file(str(root / "x.txt"))}  # x already uploaded, y new
    plan = plan_uploads([str(root / "x.txt"), str(root / "y.txt")], str(root), manifest)
    assert plan == [str(root / "y.txt")]
    (root / "x.txt").write_text("one-changed")             # x now differs -> re-upload
    plan2 = plan_uploads([str(root / "x.txt")], str(root), manifest)
    assert plan2 == [str(root / "x.txt")]


def test_manifest_roundtrip(tmp_path):
    mp = tmp_path / "m.json"
    save_manifest(str(mp), {"a": "sha"})
    assert load_manifest(str(mp)) == {"a": "sha"}
    assert load_manifest(str(tmp_path / "missing.json")) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/paperclip && python -m pytest tests/media_worker/test_blob_sync.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Write implementation**

```python
# tools/media_worker/blob_sync.py
"""Dedup-aware Blob sync planning. Blob appends random suffixes despite
--add-random-suffix false, so a naive re-run duplicates; we skip files whose
sha256 already matches the manifest. Actual `vercel blob` calls (which print to
STDERR) go through an injected runner so tests stay offline."""
from __future__ import annotations
import hashlib, json, os
from typing import Callable, Dict, List, Optional

_CHUNK = 1 << 20


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(path: str) -> Dict[str, str]:
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_manifest(path: str, m: Dict[str, str]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    json.dump(m, open(path, "w", encoding="utf-8"), indent=2, sort_keys=True)


def plan_uploads(files: List[str], root: str, manifest: Dict[str, str]) -> List[str]:
    """Files whose current sha256 differs from (or is absent in) the manifest."""
    out: List[str] = []
    for f in files:
        rel = os.path.relpath(f, root)
        if manifest.get(rel) != sha256_file(f):
            out.append(f)
    return out


def upload(files: List[str], root: str, manifest_path: str,
           runner: Optional[Callable[[List[str]], str]] = None) -> Dict[str, str]:
    """Upload only changed files (via `runner`, default `vercel blob put` capturing
    STDERR) and update the manifest. Injected runner keeps tests offline."""
    import subprocess
    manifest = load_manifest(manifest_path)
    todo = plan_uploads(files, root, manifest)
    run = runner or (lambda argv: subprocess.run(argv, capture_output=True, text=True).stderr)
    for f in todo:
        rel = os.path.relpath(f, root)
        run(["vercel", "blob", "put", f, "--add-random-suffix", "false"])
        manifest[rel] = sha256_file(f)
    save_manifest(manifest_path, manifest)
    return manifest
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/paperclip && python -m pytest tests/media_worker/test_blob_sync.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/media_worker/blob_sync.py tests/media_worker/test_blob_sync.py
git commit -m "feat(media-worker): dedup-aware Blob sync planning (sha256 manifest)"
```

---

### Task 5: Render orchestration (one take, end-to-end)

Glue transcribe -> build_cut_argv -> `cut_talking_head.py` -> `video_review_sheet.py`. Integration-tested behind a binary/asset skip (real proof runs in the image, Task 8). No auto-scheduling here; this produces the master + contact sheet only.

**Files:**
- Create: `tools/media_worker/render.py`
- Test: `tests/media_worker/test_render_smoke.py`

**Interfaces:**
- Consumes: `transcribe` (Task 3), `build_cut_argv` (Task 2).
- Produces: `render_one(edit: dict, take: str, model: str, out_dir: str, cut_script: str, sheet_script: str) -> dict` returning `{"master": path, "sheet": path, "words": path}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/media_worker/test_render_smoke.py
import os, shutil, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import pytest
from tools.media_worker.render import render_one

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("whisper-cli")),
    reason="ffmpeg + whisper-cli required (present in the worker image / local Mac)")


def test_render_one_produces_master_and_sheet(tmp_path):
    model = os.environ.get("WHISPER_MODEL",
                           os.path.expanduser("~/stock_library/.whisper_models/ggml-small.en.bin"))
    take = os.environ.get("SMOKE_TAKE")  # a real short AIPG take; set in CI/local
    if not (os.path.exists(model) and take and os.path.exists(take)):
        pytest.skip("model + SMOKE_TAKE required for the render smoke")
    res = render_one({"brand": "aipg"}, take, model, str(tmp_path),
                     cut_script=os.path.expanduser("~/avo-telemetry/scripts/cut_talking_head.py"),
                     sheet_script=os.path.expanduser("~/avo-telemetry/scripts/video_review_sheet.py"))
    assert os.path.getsize(res["master"]) > 0
    assert os.path.getsize(res["sheet"]) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/paperclip && python -m pytest tests/media_worker/test_render_smoke.py -v`
Expected: FAIL (ModuleNotFoundError) or skip.

- [ ] **Step 3: Write implementation**

```python
# tools/media_worker/render.py
"""Render one take end-to-end: transcribe -> cut -> contact sheet. No scheduling."""
from __future__ import annotations
import os, pathlib, subprocess
from tools.media_worker.transcribe import transcribe
from tools.media_worker.edit_spec import build_cut_argv


def render_one(edit: dict, take: str, model: str, out_dir: str,
               cut_script: str, sheet_script: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    stem = pathlib.Path(take).stem
    words = transcribe(take, model, out_dir)
    master = os.path.join(out_dir, f"{stem}.mp4")
    subprocess.run(build_cut_argv(edit, take, words, master, script=cut_script), check=True)
    sheet = os.path.join(out_dir, f"{stem}.review.png")
    subprocess.run(["python3", sheet_script, master, sheet], check=True)
    return {"master": master, "sheet": sheet, "words": words}
```

- [ ] **Step 4: Run the smoke test where binaries + a take exist**

Run: `cd ~/paperclip && SMOKE_TAKE=<a real short aipg take.mp4> python -m pytest tests/media_worker/test_render_smoke.py -v`
Expected: PASS (master + sheet non-empty). Skips cleanly without the fixture.

- [ ] **Step 5: Commit**

```bash
git add tools/media_worker/render.py tests/media_worker/test_render_smoke.py
git commit -m "feat(media-worker): render one take (transcribe -> cut -> contact sheet)"
```

---

### Task 6: Worker image (Dockerfile) — GATED on TP decision 4 (bake vs boot-pull) + 2 (service shape)

Debian slim + ffmpeg (apt) + whisper.cpp built from source (pinned) + Pillow + the 4 brand fonts + one Linux label font (fixes the Mac Arial path at `video_review_sheet.py:59`). Model, gated stock, and brand logos are boot-pulled from Blob (Task 4/8), not baked. No headless Chrome (the video gate is ffmpeg+PIL; audit §6).

**Files:**
- Create: `Dockerfile.media-worker`, `tools/media_worker/smoke.py`

- [ ] **Step 1: Write the smoke self-check**

```python
# tools/media_worker/smoke.py
"""In-image self-check: the three things most likely to break in a Linux port."""
import shutil, subprocess, sys
from PIL import ImageFont


def main() -> int:
    ok = True
    for b in ("ffmpeg", "ffprobe", "whisper-cli"):
        if not shutil.which(b):
            print(f"MISSING binary: {b}"); ok = False
    try:
        f = ImageFont.truetype("/opt/media/fonts/InterTight.ttf", 78)
        f.set_variation_by_name("Black")  # variable-font support (FreeType)
    except Exception as e:
        print(f"variable font FAILED: {e}"); ok = False
    print("SMOKE OK" if ok else "SMOKE FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Write the Dockerfile**

```dockerfile
# Dockerfile.media-worker
FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg git build-essential cmake ca-certificates fonts-dejavu-core curl \
    && rm -rf /var/lib/apt/lists/*

# whisper.cpp (pinned) -> whisper-cli on PATH
ARG WHISPER_REF=v1.7.2
RUN git clone --depth 1 --branch ${WHISPER_REF} https://github.com/ggerganov/whisper.cpp /tmp/whisper \
    && cmake -S /tmp/whisper -B /tmp/whisper/build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /tmp/whisper/build --target whisper-cli -j \
    && cp /tmp/whisper/build/bin/whisper-cli /usr/local/bin/whisper-cli \
    && rm -rf /tmp/whisper

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir Pillow requests

# fonts (bundled) + the pipeline scripts + the worker package
COPY assets/fonts/ /opt/media/fonts/
COPY scripts/ /app/scripts/
COPY tools/ /app/tools/
COPY services/ /app/services/

ENV FONTS_DIR=/opt/media/fonts \
    WHISPER_MODEL=/opt/media/cache/whisper/ggml-small.en.bin \
    STOCK_LIB=/opt/media/cache/stock_library \
    BRAND_ASSETS=/opt/media/cache/brand_assets \
    RENDERS_OUT=/opt/media/out

RUN python -c "import PIL; print('pillow', PIL.__version__)"
CMD ["python", "tools/media_worker/smoke.py"]
```

> NOTE: `assets/fonts/` must be present in the build context. Step 3 stages them from `~/avo-telemetry/assets/fonts/` into the repo (or a build-context copy). `scripts/` are the `~/avo-telemetry/scripts/` pipeline files; vendor them into paperclip or add avo-telemetry as a build context. The vendor-vs-submodule choice is a sub-decision to confirm with the TP alongside decision 2.

- [ ] **Step 3: Stage fonts + scripts into the build context**

```bash
mkdir -p assets/fonts scripts
cp ~/avo-telemetry/assets/fonts/*.ttf assets/fonts/
cp ~/avo-telemetry/scripts/cut_talking_head.py ~/avo-telemetry/scripts/build_short.py \
   ~/avo-telemetry/scripts/video_review_sheet.py ~/avo-telemetry/scripts/stock_fetch.py scripts/
git add assets/fonts scripts Dockerfile.media-worker tools/media_worker/smoke.py
git commit -m "build(media-worker): Dockerfile + bundled fonts/scripts + smoke check"
```

- [ ] **Step 4: Build + smoke (acceptance)**

Run: `cd ~/paperclip && docker build -f Dockerfile.media-worker -t avo-media-worker . && docker run --rm avo-media-worker`
Expected: `SMOKE OK` (ffmpeg, ffprobe, whisper-cli present; variable font loads).

- [ ] **Step 5: Fix `video_review_sheet.py:59` to use the bundled font**

Change the hardcoded `/System/Library/Fonts/Supplemental/Arial.ttf` to read `os.environ.get("LABEL_FONT", "/opt/media/fonts/InterTight.ttf")` with the existing `load_default` fallback. Commit.

---

### Task 7: Trigger handler (pull -> render -> push -> stage CMO flag) — GATED on TP decision 2

Follows the proven watchdog pattern (`POST /admin/run-watchdog`, paperclip PR #161). If the TP rules "new service", this same handler is the new service's entrypoint; if "add-on", it mounts on the existing paperclip app. Handler logic is identical either way. Stage-and-flag only: NEVER auto-schedule.

**Files:**
- Create: `services/media_worker.py`

- [ ] **Step 1: Write the handler**

```python
# services/media_worker.py
"""Video worker run: pull assets from Blob, render one take, push master + sheet
back, append REVIEW_LOG, raise a CMO flag. Stage-and-flag only (no scheduling)."""
from __future__ import annotations
import os
from tools.media_worker.render import render_one


def run_video(take: str, edit: dict) -> dict:
    model = os.environ["WHISPER_MODEL"]
    out_dir = os.environ.get("RENDERS_OUT", "/opt/media/out")
    res = render_one(
        edit, take, model, out_dir,
        cut_script=os.path.join(os.path.dirname(__file__), "..", "scripts", "cut_talking_head.py"),
        sheet_script=os.path.join(os.path.dirname(__file__), "..", "scripts", "video_review_sheet.py"),
    )
    # push master + sheet to Blob (tools.media_worker.blob_sync.upload), append
    # renders/th/REVIEW_LOG.md, and write a CMO flag to cmo_state.md via the flag
    # helper. Outward gate stays human: nothing schedules here.
    return {"status": "staged", **res}
```

- [ ] **Step 2: Wire the trigger** per the TP's decision 2 (endpoint on paperclip `app.py` mirroring `POST /admin/run-watchdog`, or the new service's FastAPI). Add a test that a request with a take id + edit returns `status == "staged"` with the render stubbed.

- [ ] **Step 3: Commit** (`feat(media-worker): staged run handler + trigger`).

---

### Task 8: Railway deploy + laptop-off proof — GATED

**Files:** Railway service config; Doppler secrets.

- [ ] **Step 1: Secrets** — `BLOB_READ_WRITE_TOKEN` (from `cd ~/worship-digital-site && vercel env pull`), plus any publish keys, into Doppler -> Railway. Nothing pasted.
- [ ] **Step 2: Deploy** the `Dockerfile.media-worker` service on Railway; healthcheck green.
- [ ] **Step 3: Boot-pull** model + gated stock + brand_assets from Blob into the cache volume; checksum-verify against the manifest (Task 4).
- [ ] **Step 4: LAPTOP-OFF PROOF (acceptance).** With Michael's Mac shut, trigger the service for one AIPG take. Show receipts: Railway logs of the run, the master + sheet objects on Blob (byte counts), a rendered-frame sample, the CMO flag raised, and NO auto-schedule. This is the Section 1 sentence, demonstrated.
- [ ] **Step 5: Cutover hygiene** — document that the render pipeline now has a cloud home; leave scheduling human-gated until the CMO grants a standing go.

**Fast-follows (separate plans, not blockers):** `build_short.py` clone path port; `stock_fetch.py` port (Pexels/Pixabay keys); Blob-poll auto-trigger; the licensed **music bed** mixed under the VO (build in the cloud version, never locally first).

---

## Self-Review

**Spec coverage (vs deliverable 142):** inventory port (Tasks 2-5, 6); Mac-isms (font path Task 6.5, env roots Task 6, logos Task 8.3); worker image (Task 6); shared-image decision (Task 6/7 gated, surfaced to TP); gate portability + no-Chrome correction (Task 6, honored); Blob I/O + dedup gotcha (Task 4); trigger model (Task 7); edit.json intake (Task 2); X-280 bug (Task 1); migration order + acceptance (Tasks 0-8); laptop-off test (Task 8.4). All §8 steps map to a task.

**Placeholder scan:** code steps carry complete code; the two genuinely TP-gated sub-decisions (vendor scripts vs submodule; endpoint-on-app vs new-service) are flagged as decisions, not hidden as TODOs. Tasks 1-5 are fully executable today.

**Type consistency:** `build_cut_argv` (Task 2) is consumed by `render_one` (Task 5) with matching signature; `transcribe` (Task 3) return (path str) is consumed by `render_one`; `plan_uploads`/`upload` (Task 4) used by Task 8.3; `tweet_length` (Task 1) is self-contained. Names checked across tasks.

## Execution Handoff

Execution is **TP-gated** (the 3 rulings in 142 §10). On the go: Tasks 1-5 are architecture-independent and run first (Task 1, the X-280 fix, is a clean standalone PR); Tasks 6-8 proceed once decisions 2 and 4 land.
