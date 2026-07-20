"""Dedup-aware Blob sync planning. Blob appends random suffixes despite
--add-random-suffix false, so a naive re-run duplicates; we skip files whose
sha256 already matches the manifest. Actual `vercel blob` calls (which print to
STDERR) go through an injected runner so tests stay offline."""
from __future__ import annotations
import hashlib, json, os, subprocess
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
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_manifest(path: str, m: Dict[str, str]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=2, sort_keys=True)


def plan_uploads(files: List[str], root: str, manifest: Dict[str, str]) -> List[str]:
    """Files whose current sha256 differs from (or is absent in) the manifest."""
    out: List[str] = []
    for f in files:
        rel = os.path.relpath(f, root)
        if manifest.get(rel) != sha256_file(f):
            out.append(f)
    return out


def _default_runner(argv: List[str]) -> str:
    r = subprocess.run(argv, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"blob put failed: {r.stderr.strip()[:200]}")
    return r.stderr


def upload(files: List[str], root: str, manifest_path: str,
           runner: Optional[Callable[[List[str]], str]] = None) -> Dict[str, str]:
    """Upload only changed files (via `runner`, default `vercel blob put` capturing
    STDERR) and update the manifest. Injected runner keeps tests offline.
    A file is only recorded in the manifest after its runner call returns
    without raising, so a failed upload is retried on the next sync instead
    of being skipped forever. Progress is saved in a `finally` block, so
    files that succeeded before a failure are still persisted; the raised
    exception then propagates to tell the caller the sync failed."""
    manifest = load_manifest(manifest_path)
    todo = plan_uploads(files, root, manifest)
    run = runner or _default_runner
    try:
        for f in todo:
            rel = os.path.relpath(f, root)
            run(["vercel", "blob", "put", f, "--add-random-suffix", "false"])
            manifest[rel] = sha256_file(f)
    finally:
        save_manifest(manifest_path, manifest)
    return manifest
