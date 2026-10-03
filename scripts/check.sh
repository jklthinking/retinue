#!/usr/bin/env bash
# Mechanised handoff gate for AGENTS.md rule 8.
#
# Every step runs even if an earlier one fails, so one invocation reports the
# whole picture; the exit status is non-zero if any step failed. The visual PNG
# inspection in rule 8 cannot be automated, so changed images are listed for a
# human (or agent) to open.
#
# Loopback and standard documentation addresses/network constants are allowed
# per match. A safe address never exempts another candidate on the same line.
#
# Rule 4 uses the same strict per-match scanner as public exports. Both the
# default and --all gates sweep all public source files, including generated
# JavaScript. Exact hash-bound synthetic/rule/license exceptions require review.
set -uo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

failures=0
step() { printf '\n== %s ==\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; failures=$((failures + 1)); }

step "test suite"
# Resolve the interpreter once. An activated venv puts `python` first, which is what a
# contributor working in one will have; a bare Debian or Ubuntu only has `python3`. Without
# this, a missing interpreter is reported as several unrelated check failures.
if command -v python >/dev/null 2>&1; then
  PY=python
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  echo "no python interpreter found: install Python 3.10 or newer, or activate your venv" >&2
  exit 1
fi

if ! "$PY" -c "import pytest" >/dev/null 2>&1; then
  echo "pytest is not installed for $PY: run  pip install -e '.[test]'" >&2
  fail "pytest"
else
  "$PY" -m pytest -q || fail "pytest"
fi

step "whitespace"
git diff --check || fail "whitespace errors in the working tree"
git diff --cached --check || fail "whitespace errors in the index"

step "compile"
"$PY" -m compileall -q adapters core scripts server tests tools >/dev/null \
  || fail "compile check"

# The construction ledger is a script step, not only a test, because it reads
# as policy: every construction of a permission-surface entity must carry a
# written reason in scripts/construction_ledger.yaml naming who decides the
# change. The pytest module proves the checker works; this step is what the
# author actually meets at handoff, and what the clean runner enforces.
step "construction ledger"
"$PY" scripts/check_construction_ledger.py || fail "construction ledger"

step "public identifier, credential, and mirror scan (whole source tree)"
# Shared per-match rules and exact reviewed exceptions are identical to the exporter.
# Results expose paths and categories only. No test directory is exempt.
"$PY" scripts/export_community.py --scan-only --source . \
  || fail "public source candidates require review"

# The panel is TypeScript, and nothing above type-checks it. A change that
# compiled cleanly in Python while the frontend failed to build got as far as
# review once, so the toolchain is required exactly when the frontend is touched
# and skipped loudly otherwise.
step "frontend type check"
frontend_touched=$(
  git diff HEAD --name-only -- webui
  git ls-files --others --exclude-standard -- webui
)
if [ -d webui/node_modules ]; then
  (cd webui && npx tsc -b) || fail "tsc -b rejected the panel sources"
elif [ -n "$frontend_touched" ]; then
  fail "this change touches webui but webui/node_modules is absent; run npm ci there"
else
  echo "skipped: webui/node_modules absent, and this change does not touch webui"
fi

step "changed images (inspect these by eye)"
images=$(
  git diff --name-only HEAD -- '*.png'
  git ls-files --others --exclude-standard -- '*.png'
)
if [ -n "$images" ]; then
  printf '%s\n' "$images" | sort -u
else
  echo "none"
fi

printf '\n'
if [ "$failures" -gt 0 ]; then
  printf 'check.sh: %d step(s) failed\n' "$failures"
  exit 1
fi
printf 'check.sh: all steps passed\n'
