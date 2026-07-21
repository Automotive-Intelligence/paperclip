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
