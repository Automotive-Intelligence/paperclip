import base64
from unittest import mock

from services import slipstream_engine as se

_CFG = {"repo": "salesdroid/automotive-intelligence", "queue_path": "automation/content-queue.md"}
_QUEUE = "# queue\n\n- [x] already done\n- [ ] What signs tell a dealer an AI tool works?\n- [ ] Another topic\n"


def _get_resp():
    r = mock.Mock(); r.ok = True
    r.json.return_value = {"sha": "sha1", "content": base64.b64encode(_QUEUE.encode()).decode()}
    return r


def test_checkoff_marks_topic_and_puts():
    put_calls = []

    def _put(url, headers=None, json=None, timeout=None):
        put_calls.append(json)
        return mock.Mock(ok=True)

    with mock.patch.object(se.requests, "get", return_value=_get_resp()), \
         mock.patch.object(se.requests, "put", side_effect=_put):
        ok = se._checkoff_topic(_CFG, "What signs tell a dealer an AI tool works?",
                                "https://automotiveintelligence.io/blog/x", "tok")
    assert ok is True
    new_text = base64.b64decode(put_calls[0]["content"]).decode()
    assert "- [x] What signs tell a dealer an AI tool works? → https://automotiveintelligence.io/blog/x" in new_text
    assert "- [ ] What signs tell a dealer an AI tool works?" not in new_text
    assert "- [ ] Another topic" in new_text  # only the one topic checked


def test_checkoff_noop_when_topic_not_in_queue():
    with mock.patch.object(se.requests, "get", return_value=_get_resp()), \
         mock.patch.object(se.requests, "put") as put:
        ok = se._checkoff_topic(_CFG, "A topic that is not in the queue at all",
                                "https://x/blog/y", "tok")
    assert ok is False
    put.assert_not_called()


def test_checkoff_idempotent_when_already_checked():
    """A topic already marked '- [x]' is a no-op: the '- [ ]' pattern no longer
    matches, so a second successful publish/check-off never double-marks the line
    or re-commits the queue (the 'idempotently' requirement). This is what keeps a
    re-run of an already-shipped topic from corrupting the queue."""
    already = ("# queue\n\n"
               "- [x] What signs tell a dealer an AI tool works? → https://x/blog/x\n"
               "- [ ] Another topic\n")
    resp = mock.Mock(); resp.ok = True
    resp.json.return_value = {"sha": "sha1",
                             "content": base64.b64encode(already.encode()).decode()}
    with mock.patch.object(se.requests, "get", return_value=resp), \
         mock.patch.object(se.requests, "put") as put:
        ok = se._checkoff_topic(_CFG, "What signs tell a dealer an AI tool works?",
                                "https://x/blog/x", "tok")
    assert ok is False
    put.assert_not_called()


# --- storefront brands (no `repo` key at all) -------------------------------
# P&P publishes to Shopify, so its config deliberately has NO `repo`. Its topic
# queue still lives on GitHub via `queue_repo`. The engine used to resolve that
# with cfg.get("queue_repo", cfg["repo"]), whose default is evaluated EAGERLY,
# so every P&P run died with KeyError: 'repo' before reaching the Shopify path.

_STOREFRONT_CFG = {
    "queue_repo": "salesdroid/avo-telemetry",
    "queue_path": "scripts/blog_queues/pp_topics.md",
}


def test_next_topic_works_without_a_repo_key():
    with mock.patch.object(se.requests, "get", return_value=_get_resp()):
        assert se._next_topic(_STOREFRONT_CFG, "tok") == (
            "What signs tell a dealer an AI tool works?")


def test_next_topic_reads_from_queue_repo_not_repo():
    """queue_repo must win even when both keys are present."""
    seen = {}

    def _get(url, headers=None, timeout=None):
        seen["url"] = url
        return _get_resp()

    cfg = dict(_STOREFRONT_CFG, repo="some/other-repo")
    with mock.patch.object(se.requests, "get", _get):
        se._next_topic(cfg, "tok")
    assert "salesdroid/avo-telemetry" in seen["url"]
    assert "some/other-repo" not in seen["url"]


def test_checkoff_works_without_a_repo_key():
    def _put(url, headers=None, json=None, timeout=None):
        r = mock.Mock(); r.ok = True
        return r

    with mock.patch.object(se.requests, "get", return_value=_get_resp()), \
         mock.patch.object(se.requests, "put", _put):
        assert se._checkoff_topic(
            _STOREFRONT_CFG, "What signs tell a dealer an AI tool works?",
            "https://paperandpurpose.co/blogs/news/x", "tok") is True
