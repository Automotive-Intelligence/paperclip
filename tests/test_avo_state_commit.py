"""avo_state_commit.update_state: happy path, idempotent skip, 409 conflict retry."""
import pytest

from services import avo_state_commit as S


def test_commit_happy_path():
    reads = [("OLD", "sha1")]
    puts = []

    def read(path, token):
        return reads[0]

    def put(path, content, sha, message, token):
        puts.append((content, sha))
        return True, 200

    out = S.update_state("f.md", lambda c: c + " NEW", "msg", "tok", read=read, put=put)
    assert out == {"committed": True}
    assert puts == [("OLD NEW", "sha1")]


def test_idempotent_transform_returns_none_skips():
    puts = []
    out = S.update_state("f.md", lambda c: None, "msg", "tok",
                         read=lambda p, t: ("X", "s"),
                         put=lambda *a: puts.append(a) or (True, 200))
    assert out == {"committed": False, "skipped": True}
    assert puts == []          # nothing written when transform says "already done"


def test_409_conflict_reretries_with_fresh_sha_then_succeeds():
    # First PUT 409s (stale sha); re-read yields a fresh sha + fresh content, retry wins.
    reads = iter([("v1", "sha1"), ("v2", "sha2")])
    put_calls = []

    def put(path, content, sha, message, token):
        put_calls.append(sha)
        return (False, 409) if len(put_calls) == 1 else (True, 200)

    out = S.update_state("f.md", lambda c: c + "!", "msg", "tok",
                         read=lambda p, t: next(reads), put=put)
    assert out == {"committed": True}
    assert put_calls == ["sha1", "sha2"]   # re-read gave the fresh sha on retry


def test_non_409_error_raises():
    with pytest.raises(RuntimeError):
        S.update_state("f.md", lambda c: c, "m", "tok",
                       read=lambda p, t: ("x", "s"), put=lambda *a: (False, 500))


def test_no_token_returns_error():
    assert S.update_state("f.md", lambda c: c, "m", "")["committed"] is False
