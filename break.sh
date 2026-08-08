#!/usr/bin/env bash
# break.sh -- produce reproducible CI failures for TraceMe.
#
#   ./break.sh <case>            create branch break/<case>, apply, commit, push
#   ./break.sh <case> --local    just apply the edits to the working tree
#   ./break.sh --list            show the cases
#
# Every case fails at a *different named step* of .github/workflows/ci.yml, so
# TraceMe's "first failing step" detection has something real to distinguish.
#
#   case          | breaks                          | fails at     | category
#   --------------|---------------------------------|--------------|-------------
#   test_failure  | a constant the tests assert on  | Run tests    | test_failure
#   dependency    | requests==99.99.99              | Install deps | dependency
#   lint_type     | unused import + undefined name  | Lint         | lint_type
#   config        | python-version: "3.99"          | Set up Pyth. | config
#   subtle        | refresh() return type changes   | Run tests    | test_failure
set -euo pipefail

CASES="test_failure dependency lint_type config subtle"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

usage() { echo "usage: ./break.sh {$(echo $CASES | tr ' ' '|')} [--local]"; exit 1; }
[ $# -ge 1 ] || usage
CASE="$1"; shift || true
LOCAL=0
[ "${1:-}" = "--local" ] && LOCAL=1
if [ "$CASE" = "--list" ]; then echo "$CASES" | tr ' ' '\n'; exit 0; fi
case " $CASES " in *" $CASE "*) ;; *) usage ;; esac

py() { python3 - "$@"; }

apply_test_failure() {
  py <<'PY'
import pathlib
p = pathlib.Path("app/auth.py")
s = p.read_text()
assert "TOKEN_TTL_SECONDS = 3600" in s, "auth.py not at baseline"
s = s.replace("TOKEN_TTL_SECONDS = 3600", "TOKEN_TTL_SECONDS = 1800")
p.write_text(s)
print("app/auth.py: TOKEN_TTL_SECONDS 3600 -> 1800")
PY
  MSG="chore(auth): shorten default token lifetime to 30 minutes"
}

apply_dependency() {
  py <<'PY'
import pathlib
p = pathlib.Path("requirements.txt")
s = p.read_text()
assert "requests" not in s, "requirements.txt not at baseline"
p.write_text(s.rstrip("\n") + "\nrequests==99.99.99\n")
print("requirements.txt: + requests==99.99.99")
PY
  MSG="build: pin requests for the upcoming webhook client"
}

apply_lint_type() {
  py <<'PY'
import pathlib
p = pathlib.Path("app/auth.py")
s = p.read_text()
assert "import json" not in s, "auth.py not at baseline"
# F401: imported but unused.
s = s.replace("import hashlib\nimport hmac\n", "import hashlib\nimport hmac\nimport json\n")
# F821: undefined name `AUDIT_LOG`.
s = s.replace(
    '    if not user:\n        raise AuthError("user is required")\n',
    '    if not user:\n        raise AuthError("user is required")\n'
    '    AUDIT_LOG.append({"event": "issue", "user": user, "at": now})\n',
)
p.write_text(s)
print("app/auth.py: + unused `import json` (F401), + undefined `AUDIT_LOG` (F821)")
PY
  MSG="feat(auth): start recording token issuance in the audit log"
}

apply_config() {
  py <<'PY'
import pathlib
p = pathlib.Path(".github/workflows/ci.yml")
s = p.read_text()
assert 'python-version: "3.11"' in s, "ci.yml not at baseline"
p.write_text(s.replace('python-version: "3.11"', 'python-version: "3.99"'))
print('.github/workflows/ci.yml: python-version 3.11 -> 3.99')
PY
  MSG="ci: move the build onto the newest Python"
}

apply_subtle() {
  # Two changes land in one commit:
  #   1. a LOUD, harmless rewrite of app/rate_limit.py (~60 lines) that the
  #      commit message advertises, and
  #   2. a QUIET 3-line change in app/auth.py where refresh() stops returning a
  #      Token and starts returning a plain dict.
  # The failing test is `test_refresh_extends_session`; nothing in the rate
  # limiter is named anything like it. The traceback points at the *test* file.
  # The only way to the real cause is deciding to open app/auth.py.
  py <<'PY'
import pathlib

auth = pathlib.Path("app/auth.py")
s = auth.read_text()
assert "return issue_token(token.user, now)" in s, "auth.py not at baseline"
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
auth.write_text(s)
print("app/auth.py: refresh() now returns a dict instead of a Token (3 lines)")

rl = pathlib.Path("app/rate_limit.py")
rl.write_text('''"""Token-bucket rate limiter.

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
''')
print("app/rate_limit.py: rewritten as a token bucket (~60 lines, harmless)")
PY
  MSG="perf(rate-limit): replace fixed window with a token bucket for burst traffic"
}

MSG=""
case "$CASE" in
  test_failure) apply_test_failure ;;
  dependency)   apply_dependency ;;
  lint_type)    apply_lint_type ;;
  config)       apply_config ;;
  subtle)       apply_subtle ;;
esac

if [ "$LOCAL" = "1" ]; then
  echo "--local: edits applied to the working tree, no git operations."
  exit 0
fi

BRANCH="break/$CASE"
git checkout -B "$BRANCH" >/dev/null 2>&1 || { echo "git checkout -B $BRANCH failed"; exit 1; }
git add -A
git commit -q -m "$MSG"
git push -u origin "$BRANCH" --force
echo
echo "pushed $BRANCH  --  commit message: $MSG"
echo "watch it go red, then run:  python scripts/run_agent.py <owner>/<repo> --branch $BRANCH"
git checkout - >/dev/null 2>&1 || true
