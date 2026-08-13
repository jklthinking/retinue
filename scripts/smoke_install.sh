#!/usr/bin/env bash
# Install a built wheel into a throwaway environment, start the hub, and
# require GET /api/health to report ok. No credentials are created.
set -euo pipefail

if [ "${1:-}" = "" ]; then
    echo "usage: bash scripts/smoke_install.sh path/to/retinue-*.whl" >&2
    exit 2
fi
wheel=$1
if [ ! -f "$wheel" ]; then
    echo "smoke: wheel not found" >&2
    exit 2
fi

port=${SMOKE_PORT:-19219}
workdir=$(mktemp -d)
server_pid=""
cleanup() {
    if [ -n "$server_pid" ]; then
        kill "$server_pid" 2>/dev/null || true
        wait "$server_pid" 2>/dev/null || true
    fi
    rm -rf "$workdir"
}
trap cleanup EXIT

python3 -m venv "$workdir/venv"
# shellcheck disable=SC1091
. "$workdir/venv/bin/activate"
python -m pip install --upgrade pip
python -m pip install "${wheel}[server]"
python -m server.main --data-dir "$workdir/data" migrate
python -m server.main --data-dir "$workdir/data" serve --host 127.0.0.1 --port "$port" &
server_pid=$!

tries=0
while [ "$tries" -lt 30 ]; do
    if out=$(curl -fsS "http://127.0.0.1:${port}/api/health" 2>/dev/null); then
        printf '%s\n' "$out"
        printf '%s\n' "$out" | grep -q '"status":"ok"' || {
            echo "smoke: health payload was not ok" >&2
            exit 1
        }
        echo "smoke: health ok"
        exit 0
    fi
    tries=$((tries + 1))
    sleep 1
done

echo "smoke: hub did not become ready" >&2
exit 1
