from services import slipstream_github as sg


class _HTTP:
    """Returns a scripted sequence of check-run states, then records the merge."""
    def __init__(self, conclusions):
        self.conclusions = list(conclusions)
        self.merged = False

    def __call__(self, method, url, token, json_body=None):
        if url.endswith("/pulls/30"):
            return {"head": {"sha": "abc"}}
        if url.endswith("/commits/abc/check-runs"):
            c = self.conclusions.pop(0) if self.conclusions else "success"
            return {"check_runs": [
                {"name": "Vercel Preview Comments", "conclusion": "success"},
                {"name": "Vercel", "status": ("completed" if c else "in_progress"), "conclusion": c},
            ]}
        if url.endswith("/pulls/30/merge"):
            self.merged = True
            return {"merged": True}
        raise AssertionError(f"unexpected {method} {url}")


def test_merges_when_vercel_success():
    http = _HTTP([None, "success"])  # pending once, then success
    out = sg.merge_when_green("salesdroid/automotive-intelligence",
                              "https://github.com/salesdroid/automotive-intelligence/pull/30",
                              "tok", http=http, poll_sleep=0)
    assert out["merged"] is True
    assert http.merged is True


def test_holds_when_vercel_fails():
    http = _HTTP(["failure"])
    out = sg.merge_when_green("salesdroid/automotive-intelligence",
                              "https://github.com/salesdroid/automotive-intelligence/pull/30",
                              "tok", http=http, poll_sleep=0)
    assert out["merged"] is False
    assert "failure" in out["reason"].lower()
    assert http.merged is False


def test_timeout_holds():
    http = _HTTP([None, None, None])
    out = sg.merge_when_green("salesdroid/automotive-intelligence",
                              "https://github.com/salesdroid/automotive-intelligence/pull/30",
                              "tok", http=http, poll_sleep=0, timeout_polls=3)
    assert out["merged"] is False
    assert "timeout" in out["reason"].lower()
    assert http.merged is False
