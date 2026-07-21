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


# --------------------------------------------------------------------------------
# Deploy-verified only, below this line: cannot run offline (needs BLOB_READ_WRITE_TOKEN
# + network + the `vercel` CLI), so this is NOT exercised by the unit tests above and is
# NOT executed as part of implementing this task. Written cleanly, reviewed by eye,
# to be confirmed at actual container boot.
if __name__ == "__main__":  # pragma: no cover
    import re
    import subprocess
    import sys

    import requests

    def _strip_blob_suffix(basename: str) -> str:
        """Vercel Blob appends a random suffix to the stored filename (the upload
        side in blob_sync.py hits the same thing even when it asks for
        --add-random-suffix false); strip it back to the logical name, e.g.
        'ggml-small.en-K7f2QxLp9a.bin' -> 'ggml-small.en.bin'."""
        return re.sub(r"-[A-Za-z0-9_-]{10,}(?=\.[^./]+$)", "", basename)

    def _real_lister(prefix: str) -> List[dict]:
        """`vercel blob list --prefix <prefix>` prints its table to STDERR; capture
        both streams (2>&1) and parse "<url> ... <size> B" lines. Blob's `list`
        does not expose a content hash, so the byte size is used as a stable dedup
        proxy: fine for static assets (a whisper model, gated stock clips) that do
        not change size incidentally between boots. If real content verification
        is ever needed, it belongs upstream in blob_sync.py's upload-time sha256,
        published alongside the object."""
        r = subprocess.run(["vercel", "blob", "list", "--prefix", prefix],
                            capture_output=True, text=True)
        combined = (r.stdout or "") + (r.stderr or "")
        entries: List[dict] = []
        for line in combined.splitlines():
            m = re.search(r"(https://\S+)\s+(\d+)\s*B\b", line.strip())
            if not m:
                continue
            url, size = m.group(1), int(m.group(2))
            logical = _strip_blob_suffix(url.rsplit("/", 1)[-1])
            entries.append({"pathname": logical, "url": url, "size": size, "sha": f"size:{size}"})
        return entries

    def _real_fetcher(url: str, dest: str) -> str:
        """Download the object URL with BLOB_READ_WRITE_TOKEN as a bearer
        Authorization header. Returns "" (no independently computed hash); boot_pull
        then falls back to the listing's size-proxy sha via `sha or item.get("sha")`."""
        token = os.environ["BLOB_READ_WRITE_TOKEN"]
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"},
                             stream=True, timeout=600)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                if chunk:
                    f.write(chunk)
        return ""

    # Model: single object stored under the ".whisper_models/" prefix. dest_root is
    # the WHISPER_MODEL directory so dest_root + logical-basename ("ggml-small.en.bin")
    # lands exactly on the WHISPER_MODEL path the rest of the pipeline (transcribe.py,
    # services/media_worker.py) already reads from.
    model_dest = os.environ.get(
        "WHISPER_MODEL", os.path.expanduser("~/stock_library/.whisper_models/ggml-small.en.bin"))
    model_root = os.path.dirname(model_dest) or "."

    # Gated stock: whatever build_short.py's LIB reads from (STOCK_LIB, defaulting to
    # ~/stock_library). Stored under the "stock/" prefix on the Blob side.
    stock_lib = os.environ.get("STOCK_LIB", os.path.expanduser("~/stock_library"))

    try:
        boot_pull([".whisper_models/"], model_root,
                  os.path.join(model_root, "boot_pull_manifest.json"),
                  lister=_real_lister, fetcher=_real_fetcher)
        boot_pull(["stock/"], stock_lib,
                  os.path.join(stock_lib, "boot_pull_manifest.json"),
                  lister=_real_lister, fetcher=_real_fetcher)
    except Exception as e:
        # non-fatal: docker-entrypoint.sh treats a nonzero exit here as "skipped or
        # partial" and still execs the container CMD. A failed item is simply not
        # recorded in its manifest, so the next boot retries it.
        print(f"boot-pull error: {e}", file=sys.stderr)
        sys.exit(1)
