#!/usr/bin/env bash
# Mechanised handoff gate for AGENTS.md rule 8.
#
# Every step runs even if an earlier one fails, so one invocation reports the
# whole picture; the exit status is non-zero if any step failed. The visual PNG
# inspection in rule 8 cannot be automated, so changed images are listed for a
# human (or agent) to open.
#
# Reserved and documentation CIDR ranges (RFC 1918, RFC 6598, RFC 5737) are
# allowlisted below alongside loopback: they name no machine, and rejecting them
# only pushes authors into writing the same constant obscurely to get past this
# scan, which is worse than the literal.
#
# Rule 4 (no live identifiers, machine paths, or credentials) is enforced
# against the lines this change adds, which is what a handoff gate is for. Pass
# --all to sweep every tracked file instead; that mode reports pre-existing
# debt as well and is not expected to be clean.
set -uo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

scan_all=0
[ "${1:-}" = "--all" ] && scan_all=1

failures=0
step() { printf '\n== %s ==\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; failures=$((failures + 1)); }

TEXT_GLOBS=('*.py' '*.md' '*.sh' '*.toml' '*.yaml' '*.yml' '*.ts' '*.tsx' '*.json' '*.html')

# Emit the content to scan, one grep-able line per source line. Added lines have
# their diff marker stripped: leaving it made '+@pytest.fixture' look like an
# e-mail address, which failed a clean change and then vanished once the change
# was committed and the diff went empty.
scan_source() {
  if [ "$scan_all" -eq 1 ]; then
    git ls-files -z -- "${TEXT_GLOBS[@]}" | xargs -0 grep -nHI '' 2>/dev/null
    return
  fi
  git diff HEAD -U0 --no-color -- "${TEXT_GLOBS[@]}" \
    | grep -E '^\+' | grep -vE '^\+\+\+' | sed 's/^+//'
  git ls-files -z --others --exclude-standard -- "${TEXT_GLOBS[@]}" \
    | xargs -0 -r grep -nHI '' 2>/dev/null
}

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

step "identifier scan ($([ "$scan_all" -eq 1 ] && echo 'whole tree' || echo 'this change'))"
identifiers=$(
  scan_source | grep -EI '([0-9]{1,3}\.){3}[0-9]{1,3}|/(root|home|Users)/|[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' \
    | grep -vE '127\.0\.0\.1|0\.0\.0\.0|localhost' \
    | grep -vE '(10|100\.64|127|169\.254|172\.16|192\.0\.2|192\.168|198\.51\.100|203\.0\.113)\.[0-9.]*/[0-9]{1,2}'
)
if [ -n "$identifiers" ]; then
  printf '%s\n' "$identifiers"
  fail "identifier scan (non-loopback address, machine path, or e-mail)"
fi

step "credential scan ($([ "$scan_all" -eq 1 ] && echo 'whole tree' || echo 'this change'))"
credentials=$(
  scan_source | grep -EI 'sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(rtn|rts|rtd)_[A-Za-z0-9_-]{30,}|(app_secret|client_secret|secret_key|password)[[:space:]]*[:=][[:space:]]*[\"'][^\"']{8,}'
)
if [ -n "$credentials" ]; then
  printf '%s\n' "$credentials"
  fail "credential scan"
fi

# Internal cloud mirror hostnames. The pattern is assembled from pieces so
# this file does not itself contain the fingerprint it is hunting.
step "mirror fingerprint ($([ "$scan_all" -eq 1 ] && echo 'whole tree' || echo 'this change'))"
_cloud=tencent
mirrors=$(
  scan_source | grep -EI "${_cloud}yun|${_cloud}cloudcr|mirrors\\.cloud\\.${_cloud}\\.com" || true
)
if [ -n "$mirrors" ]; then
  printf '%s\n' "$mirrors"
  fail "mirror fingerprint (internal cloud registry or package mirror)"
fi

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
