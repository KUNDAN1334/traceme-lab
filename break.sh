#!/usr/bin/env bash
# break.sh -- produce reproducible CI failures for TraceMe. (macOS / Linux)
#
#   ./break.sh <case>            create branch break/<case>, apply, commit, push
#   ./break.sh <case> --local    just apply the edits to the working tree
#   ./break.sh --list            show the cases
#
# Windows: use ./break.ps1 instead. PowerShell cannot execute a .sh file -- it
# does nothing at all, silently, with no error to tell you why.
#
# The edits live in apply_break.py, shared with break.ps1, so the two platforms
# cannot drift. This file is only the git half.
#
#   case          | breaks                          | fails at     | category
#   --------------|---------------------------------|--------------|-------------
#   test_failure  | a constant the tests assert on  | Run tests    | test_failure
#   dependency    | requests==99.99.99              | Install deps | dependency
#   lint_type     | unused import + undefined name  | Lint         | lint_type
#   config        | python-version: "3.99"          | Set up Pyth. | config
#   subtle        | refresh() return type changes   | Run tests    | test_failure
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PY="$(command -v python3 || command -v python)"
[ -n "$PY" ] || { echo "python3 not found on PATH"; exit 1; }

usage() {
  echo "usage: ./break.sh {$($PY apply_break.py --list | tr '\n' '|' | sed 's/|$//')} [--local]"
  exit 1
}

[ $# -ge 1 ] || usage
CASE="$1"; shift || true
if [ "$CASE" = "--list" ]; then exec "$PY" apply_break.py --list; fi
LOCAL=0
[ "${1:-}" = "--local" ] && LOCAL=1

# ---- apply the edits -------------------------------------------------------
OUTPUT="$("$PY" apply_break.py "$CASE")" || exit 1
MSG="$(printf '%s\n' "$OUTPUT" | sed -n 's/^COMMIT_MESSAGE=//p')"
printf '%s\n' "$OUTPUT" | grep -v '^COMMIT_MESSAGE='
[ -n "$MSG" ] || { echo "apply_break.py did not report a commit message"; exit 1; }

if [ "$LOCAL" = "1" ]; then
  echo "--local: edits applied to the working tree, no git operations."
  exit 0
fi

# ---- git -------------------------------------------------------------------
BRANCH="break/$CASE"
git checkout -B "$BRANCH" >/dev/null 2>&1 || { echo "git checkout -B $BRANCH failed"; exit 1; }
git add -A
git commit -q -m "$MSG"
git push -u origin "$BRANCH" --force

echo
echo "pushed $BRANCH  --  commit message: $MSG"
echo "Now WAIT for it to go red on the Actions tab, then run:"
echo "  python scripts/run_agent.py <you>/traceme-lab --branch $BRANCH"
git checkout - >/dev/null 2>&1 || true
