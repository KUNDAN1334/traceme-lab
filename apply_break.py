#!/usr/bin/env python
"""Apply one reproducible break to the working tree. No git, no shell.

Single source of truth for *what* each break changes. `break.sh` (macOS/Linux)
and `break.ps1` (Windows) are thin wrappers that call this and then do the git
work -- because the edits are the part that must not drift between platforms,
and Python is the one interpreter both platforms already have.

    python apply_break.py <case>     apply the edits, print what changed
    python apply_break.py --list     print the case names, one per line

On success the last line of stdout is `COMMIT_MESSAGE=<subject>`, which is what
the wrappers read. Exits 1 if the tree is not at baseline, so applying a break
twice complains instead of silently corrupting the file.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent


class NotAtBaseline(RuntimeError):
    """The file has already been edited; refuse rather than double-apply."""


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8")


def _require(condition: bool, rel: str) -> None:
    if not condition:
        raise NotAtBaseline(
            f"{rel} is not at baseline -- a break is already applied. "
            "Run `git checkout -- .` (or switch back to main) and try again."
        )


# --------------------------------------------------------------------------
# the five breaks
# --------------------------------------------------------------------------
def test_failure() -> tuple[str, list[str]]:
    s = _read("app/auth.py")
    _require("TOKEN_TTL_SECONDS = 3600" in s, "app/auth.py")
    _write("app/auth.py", s.replace("TOKEN_TTL_SECONDS = 3600", "TOKEN_TTL_SECONDS = 1800"))
    return (
        "chore(auth): shorten default token lifetime to 30 minutes",
        ["app/auth.py: TOKEN_TTL_SECONDS 3600 -> 1800"],
    )


def dependency() -> tuple[str, list[str]]:
    s = _read("requirements.txt")
    _require("requests" not in s, "requirements.txt")
    _write("requirements.txt", s.rstrip("\n") + "\nrequests==99.99.99\n")
    return (
        "build: pin requests for the upcoming webhook client",
        ["requirements.txt: + requests==99.99.99"],
    )


def lint_type() -> tuple[str, list[str]]:
    s = _read("app/auth.py")
    _require("import json" not in s, "app/auth.py")
    # F401: imported but unused.
    s = s.replace("import hashlib\nimport hmac\n", "import hashlib\nimport hmac\nimport json\n")
    # F821: undefined name `AUDIT_LOG`.
    s = s.replace(
        '    if not user:\n        raise AuthError("user is required")\n',
        '    if not user:\n        raise AuthError("user is required")\n'
        '    AUDIT_LOG.append({"event": "issue", "user": user, "at": now})\n',
    )
    _write("app/auth.py", s)
    return (
        "feat(auth): start recording token issuance in the audit log",
        ["app/auth.py: + unused `import json` (F401), + undefined `AUDIT_LOG` (F821)"],
    )


def config() -> tuple[str, list[str]]:
    rel = ".github/workflows/ci.yml"
    s = _read(rel)
    _require('python-version: "3.11"' in s, rel)
    _write(rel, s.replace('python-version: "3.11"', 'python-version: "3.99"'))
    return (
        "ci: move the build onto the newest Python",
        [f"{rel}: python-version 3.11 -> 3.99"],
    )


TOKEN_BUCKET = '''"""Token-bucket rate limiter.

Replaces the old fixed-window counter, which rejected legitimate bursts that
arrived right after a window boundary. Buckets refill continuously at
`limit / window_seconds` tokens per second and are capped at `burst`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


@dataclass
class RateLimiter:
    """Continuously refilling token bucket keyed by client id."""

    limit: int = 60
    window_seconds: int = 60
    burst: int | None = None
    _buckets: dict[str, _Bucket] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("limit must be positive")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._capacity = float(self.burst if self.burst is not None else self.limit)
        self._rate = self.limit / self.window_seconds

    def _bucket(self, client_id: str, now: float) -> _Bucket:
        bucket = self._buckets.get(client_id)
        if bucket is None:
            bucket = _Bucket(tokens=self._capacity, updated_at=now)
            self._buckets[client_id] = bucket
        return bucket

    def _refill(self, bucket: _Bucket, now: float) -> None:
        elapsed = max(0.0, now - bucket.updated_at)
        bucket.tokens = min(self._capacity, bucket.tokens + elapsed * self._rate)
        bucket.updated_at = now

    def allow(self, client_id: str, now: float) -> bool:
        """Spend one token; report whether the client was under the limit."""
        bucket = self._bucket(client_id, now)
        self._refill(bucket, now)
        if bucket.tokens < 1.0:
            return False
        bucket.tokens -= 1.0
        return True

    def remaining(self, client_id: str, now: float) -> int:
        bucket = self._bucket(client_id, now)
        self._refill(bucket, now)
        return int(bucket.tokens)

    def retry_after(self, client_id: str, now: float) -> float:
        """Seconds until one more token is available. 0.0 when allowed now."""
        bucket = self._bucket(client_id, now)
        self._refill(bucket, now)
        if bucket.tokens >= 1.0:
            return 0.0
        return (1.0 - bucket.tokens) / self._rate

    def reset(self, client_id: str) -> None:
        self._buckets.pop(client_id, None)
'''


def subtle() -> tuple[str, list[str]]:
    """The most important artifact in this project.

    Two changes land in one commit:
      1. a LOUD, harmless rewrite of app/rate_limit.py (~80 lines) that the
         commit message advertises, and
      2. a QUIET 3-line change in app/auth.py where refresh() stops returning a
         Token and starts returning a plain dict.

    The failing test is `test_refresh_extends_session`; nothing in the rate
    limiter is named anything like it, and nothing imports it. The traceback
    points at the *test* file. The only route to the real cause is deciding to
    open app/auth.py.
    """
    s = _read("app/auth.py")
    _require("return issue_token(token.user, now)" in s, "app/auth.py")
    s = s.replace(
        '''    if is_expired(token, now):
        raise AuthError("cannot refresh an expired token")
    return issue_token(token.user, now)''',
        '''    if is_expired(token, now):
        raise AuthError("cannot refresh an expired token")
    fresh = issue_token(token.user, now)
    # Serialise here so callers can hand the result straight to json.dumps.
    return {"value": fresh.value, "user": fresh.user, "expires_at": fresh.expires_at}''',
    )
    _write("app/auth.py", s)
    _write("app/rate_limit.py", TOKEN_BUCKET)
    return (
        "perf(rate-limit): replace fixed window with a token bucket for burst traffic",
        [
            "app/auth.py: refresh() now returns a dict instead of a Token (3 lines)",
            "app/rate_limit.py: rewritten as a token bucket (~80 lines, harmless)",
        ],
    )


CASES = {
    "test_failure": test_failure,
    "dependency": dependency,
    "lint_type": lint_type,
    "config": config,
    "subtle": subtle,
}


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply one reproducible CI break.")
    ap.add_argument("case", nargs="?", choices=sorted(CASES, key=list(CASES).index))
    ap.add_argument("--list", action="store_true", help="print the case names")
    args = ap.parse_args()

    if args.list or not args.case:
        print("\n".join(CASES))
        return 0 if args.list else 2

    try:
        message, changes = CASES[args.case]()
    except NotAtBaseline as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for line in changes:
        print(line)
    print(f"COMMIT_MESSAGE={message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
