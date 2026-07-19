from services.routine_auth import routine_token_valid


def test_valid_bearer_matches_expected():
    assert routine_token_valid("Bearer abc123", "abc123") is True


def test_wrong_token_rejected():
    assert routine_token_valid("Bearer wrong", "abc123") is False


def test_empty_expected_never_grants():
    # A blank SLIPSTREAM_ROUTINE_TOKEN must never authorize anything.
    assert routine_token_valid("Bearer ", "") is False
    assert routine_token_valid("Bearer anything", "") is False
    assert routine_token_valid("Bearer anything", None) is False


def test_missing_or_malformed_header_rejected():
    assert routine_token_valid(None, "abc123") is False
    assert routine_token_valid("abc123", "abc123") is False          # no 'Bearer '
    assert routine_token_valid("Bearer", "abc123") is False


def test_whitespace_trimmed():
    assert routine_token_valid("Bearer  abc123  ", "abc123") is True
