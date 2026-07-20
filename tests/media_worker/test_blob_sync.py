import os, sys, json
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tools.media_worker.blob_sync import sha256_file, load_manifest, save_manifest, plan_uploads, upload


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


def test_upload_uploads_only_changed_and_records_them(tmp_path):
    root = tmp_path
    (root / "a.txt").write_text("A")
    (root / "b.txt").write_text("B")
    manifest_path = tmp_path / "manifest.json"

    calls = []

    def stub(argv):
        calls.append(argv)
        return ""

    m = upload([str(root / "a.txt"), str(root / "b.txt")], str(root),
               str(manifest_path), runner=stub)
    assert len(calls) == 2
    assert set(m.keys()) == {"a.txt", "b.txt"}

    # second sync with the same, unchanged files -> runner not called again
    calls2 = []

    def stub2(argv):
        calls2.append(argv)
        return ""

    m2 = upload([str(root / "a.txt"), str(root / "b.txt")], str(root),
                str(manifest_path), runner=stub2)
    assert calls2 == []
    assert set(m2.keys()) == {"a.txt", "b.txt"}


def test_upload_failure_is_not_recorded(tmp_path):
    root = tmp_path
    (root / "good.txt").write_text("good")
    (root / "bad.txt").write_text("bad")
    manifest_path = tmp_path / "manifest.json"

    def stub(argv):
        path_arg = argv[3]
        if os.path.basename(path_arg) == "bad.txt":
            raise RuntimeError("blob put failed: simulated network error")
        return ""

    with pytest.raises(RuntimeError):
        upload([str(root / "good.txt"), str(root / "bad.txt")], str(root),
               str(manifest_path), runner=stub)

    saved = load_manifest(str(manifest_path))
    assert "bad.txt" not in saved  # failed upload must retry next sync
    assert "good.txt" in saved  # progress made before the failure is kept
