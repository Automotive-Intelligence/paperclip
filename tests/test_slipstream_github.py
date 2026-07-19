import base64

from services import slipstream_github as sg


class FakeHTTP:
    """Records calls and returns canned responses keyed by (method, url-suffix)."""
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, token, json_body=None):
        self.calls.append((method, url, json_body))
        if url.endswith("/git/ref/heads/main"):
            return {"object": {"sha": "base123"}}
        if url.endswith("/git/refs"):
            return {"ref": json_body["ref"]}
        if "/contents/" in url:
            return {"content": {"path": url.split("/contents/")[1]}}
        if url.endswith("/pulls"):
            return {"html_url": "https://github.com/salesdroid/automotive-intelligence/pull/42"}
        raise AssertionError(f"unexpected call {method} {url}")


def test_publish_creates_branch_files_and_pr():
    http = FakeHTTP()
    files = {
        "src/content/blog/post.mdx": "---\ntitle: x\n---\nbody",
        "public/blog/post-hero.png": b"PNGDATA",
    }
    url = sg.publish_post(
        repo="salesdroid/automotive-intelligence",
        branch="slipstream/post-2026-07-19",
        files=files,
        pr_title="content: post",
        pr_body="checklist ok",
        token="github_pat_x",
        http=http,
    )
    assert url == "https://github.com/salesdroid/automotive-intelligence/pull/42"

    methods = [(m, u.split("github.com/repos/salesdroid/automotive-intelligence")[-1]) for m, u, _ in http.calls]
    # base ref read, branch created, 2 files PUT, PR opened
    assert ("GET", "/git/ref/heads/main") in methods
    assert ("POST", "/git/refs") in methods
    assert sum(1 for m, u, _ in http.calls if "/contents/" in u) == 2
    assert ("POST", "/pulls") in methods

    # binary file was base64-encoded
    png_put = [j for m, u, j in http.calls if "post-hero.png" in u][0]
    assert base64.b64decode(png_put["content"]) == b"PNGDATA"
    # branch ref points at base sha
    refs_call = [j for m, u, j in http.calls if u.endswith("/git/refs")][0]
    assert refs_call["sha"] == "base123"
    assert refs_call["ref"] == "refs/heads/slipstream/post-2026-07-19"
