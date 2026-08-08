import pytest

from app.auth import (
    TOKEN_TTL_SECONDS,
    AuthError,
    Token,
    hash_password,
    is_expired,
    issue_token,
    refresh,
    verify_password,
)


def test_hash_password_is_not_plaintext_and_is_stable():
    digest = hash_password("correct-horse")
    assert digest != "correct-horse"
    assert digest == hash_password("correct-horse")
    assert len(digest) == 64


def test_verify_password_roundtrip():
    digest = hash_password("correct-horse")
    assert verify_password("correct-horse", digest) is True
    assert verify_password("wrong-horse-x", digest) is False


def test_short_password_is_rejected():
    with pytest.raises(AuthError):
        hash_password("short")


def test_issue_token_sets_ttl():
    # The published contract is "tokens last one hour". Asserting the literal
    # rather than the constant is deliberate: a test written as
    # `now + TOKEN_TTL_SECONDS` passes no matter what the constant becomes,
    # which makes it useless as a guard (and useless as a CI break).
    assert TOKEN_TTL_SECONDS == 3600
    token = issue_token("alice", now=1_000)
    assert isinstance(token, Token)
    assert token.user == "alice"
    assert token.expires_at == 4_600
    assert is_expired(token, now=4_600) is True
    assert is_expired(token, now=4_599) is False


def test_refresh_extends_session():
    token = issue_token("alice", now=1_000)
    renewed = refresh(token, now=2_000)
    assert renewed.user == "alice"
    assert renewed.expires_at == 2_000 + TOKEN_TTL_SECONDS
    assert renewed.value != token.value
