import base64

import pytest

from services import slipstream_github as sg


class FakeHTTP:
    """Records calls and returns canned responses keyed by (method, url-suffix).

    `contents_get` controls how a GET on /contents/<path>?ref=<branch> behaves
    (the existence probe the publish path uses to decide create-vs-update):
      - "missing" (default): raise a 404 PublishError, i.e. the file does not
        exist yet -> a CREATE (no sha).
      - a sha string: the file already exists on the branch -> an UPDATE and the
        PUT body must carry that sha.
    """

    def __init__(self, contents_get="missing"):
        self.calls = []
        self.contents_get = contents_get

    def __call__(self, method, url, token, json_body=None):
        self.calls.append((method, url, json_body))
        if url.endswith("/git/ref/heads/main"):
            return {"object": {"sha": "base123"}}
        if url.endswith("/git/refs"):
            return {"ref": json_body["ref"]}
        if "/contents/" in url and method == "GET":
            if self.contents_get == "missing":
                raise sg.PublishError(f"GET {url} -> 404: {{\"message\":\"Not Found\"}}")
            return {"sha": self.contents_get, "content": "existing", "path": url.split("/contents/")[1]}
        if "/contents/" in url and method == "PUT":
            return {"content": {"path": url.split("/contents/")[1]}}
        if url.endswith("/pulls"):
            return {"html_url": "https://github.com/salesdroid/automotive-intelligence/pull/42"}
        raise AssertionError(f"unexpected call {method} {url}")


def _put_bodies(http):
    return [(u, j) for m, u, j in http.calls if "/contents/" in u and m == "PUT"]


def test_publish_creates_branch_files_and_pr():
    http = FakeHTTP()  # files do not exist yet -> pure create path (MDX brands)
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
    assert len(_put_bodies(http)) == 2
    assert ("POST", "/pulls") in methods

    # binary file was base64-encoded
    png_put = [j for m, u, j in http.calls if "post-hero.png" in u and m == "PUT"][0]
    assert base64.b64decode(png_put["content"]) == b"PNGDATA"
    # branch ref points at base sha
    refs_call = [j for m, u, j in http.calls if u.endswith("/git/refs")][0]
    assert refs_call["sha"] == "base123"
    assert refs_call["ref"] == "refs/heads/slipstream/post-2026-07-19"


def test_update_existing_file_includes_sha():
    """WD (ts_posts_array) UPDATES an existing file (src/content/posts.ts). The
    GitHub Contents API requires the current blob sha to update -> the PUT body
    MUST carry the sha the existence probe returned."""
    http = FakeHTTP(contents_get="blobsha999")
    files = {"src/content/posts.ts": "export const posts = [/* ... */]"}
    sg.publish_post(
        repo="salesdroid/worship-digital",
        branch="slipstream/foo-2026-07-29",
        files=files,
        pr_title="content: foo",
        pr_body="body",
        token="github_pat_x",
        http=http,
    )
    # the existence probe read the file on the target branch
    assert any(
        m == "GET" and "/contents/src/content/posts.ts" in u and "ref=slipstream/foo-2026-07-29" in u
        for m, u, _ in http.calls
    )
    puts = _put_bodies(http)
    assert len(puts) == 1
    _, body = puts[0]
    assert body["sha"] == "blobsha999"


def test_create_new_file_omits_sha_on_404():
    """A file that does not exist (404 on the probe) is a CREATE -> no sha, and
    the existence probe still runs against the target branch."""
    http = FakeHTTP(contents_get="missing")
    files = {"src/content/blog/brand-new.mdx": "---\ntitle: y\n---\nbody"}
    sg.publish_post(
        repo="salesdroid/automotive-intelligence",
        branch="slipstream/brand-new-2026-07-29",
        files=files,
        pr_title="content: brand new",
        pr_body="body",
        token="github_pat_x",
        http=http,
    )
    assert any(
        m == "GET" and "/contents/src/content/blog/brand-new.mdx" in u
        for m, u, _ in http.calls
    )
    puts = _put_bodies(http)
    assert len(puts) == 1
    _, body = puts[0]
    assert "sha" not in body


def test_mdx_create_path_carries_no_sha_regression():
    """Regression: the MDX-brand create path (new .mdx + hero png) never sends a
    sha and still opens the PR."""
    http = FakeHTTP(contents_get="missing")
    files = {
        "src/content/blog/post.mdx": "---\ntitle: x\n---\nbody",
        "public/blog/post-hero.png": b"PNGDATA",
    }
    url = sg.publish_post(
        repo="salesdroid/automotive-intelligence",
        branch="slipstream/post-2026-07-19",
        files=files,
        pr_title="content: post",
        pr_body="ok",
        token="github_pat_x",
        http=http,
    )
    assert url.endswith("/pull/42")
    puts = _put_bodies(http)
    assert len(puts) == 2
    assert all("sha" not in body for _, body in puts)


def test_publish_raises_on_non_404_probe_error():
    """A non-404 error on the existence probe (e.g. auth/500) must NOT be
    swallowed as 'create' -- it surfaces as a PublishError."""

    class Boom(FakeHTTP):
        def __call__(self, method, url, token, json_body=None):
            if "/contents/" in url and method == "GET":
                self.calls.append((method, url, json_body))
                raise sg.PublishError(f"GET {url} -> 500: server on fire")
            return super().__call__(method, url, token, json_body)

    http = Boom()
    with pytest.raises(sg.PublishError):
        sg.publish_post(
            repo="salesdroid/worship-digital",
            branch="slipstream/foo-2026-07-29",
            files={"src/content/posts.ts": "x"},
            pr_title="t",
            pr_body="b",
            token="github_pat_x",
            http=http,
        )
