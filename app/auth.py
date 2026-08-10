"""Minimal token-based auth helpers.

Deliberately small so that a CI failure here is easy to reason about once you
are actually looking at the file -- which is the whole point of the lab repo.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

# Tokens live for one hour. The tests assert on this constant directly, which
# is what makes it a good target for the `test_failure` break.
TOKEN_TTL_SECONDS = 3600

MIN_PASSWORD_LENGTH = 8

_SALT = b"traceme-lab-salt"


@dataclass(frozen=True)
class Token:
    """An opaque bearer token with an absolute expiry."""

    value: str
    user: str
    expires_at: int


class AuthError(Exception):
    """Raised when a credential or token is not acceptable."""


def hash_password(password: str) -> str:
    """Return a hex digest for `password`."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return hashlib.sha256(_SALT + password.encode("utf-8")).hexdigest()


def verify_password(password: str, hashed: str) -> bool:
    """Constant-time comparison of `password` against a stored digest."""
    try:
        candidate = hash_password(password)
    except AuthError:
        return False
    return hmac.compare_digest(candidate, hashed)


def issue_token(user: str, now: int) -> Token:
    """Mint a fresh token for `user` valid for TOKEN_TTL_SECONDS from `now`."""
    if not user:
        raise AuthError("user is required")
    raw = f"{user}:{now}".encode("utf-8")
    value = hashlib.sha256(_SALT + raw).hexdigest()[:32]
    return Token(value=value, user=user, expires_at=now + TOKEN_TTL_SECONDS)


def is_expired(token: Token, now: int) -> bool:
    """True when `token` is no longer valid at `now` (expiry is exclusive)."""
    return now >= token.expires_at


def refresh(token: Token, now: int) -> Token:
    """Return a new Token for the same user, extending the session from `now`."""
    if is_expired(token, now):
        raise AuthError("cannot refresh an expired token")
    fresh = issue_token(token.user, now)
    # Serialise here so callers can hand the result straight to json.dumps.
    return {"value": fresh.value, "user": fresh.user, "expires_at": fresh.expires_at}
