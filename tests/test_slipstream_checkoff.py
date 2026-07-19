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
