#!/bin/sh
# Start the v0.2 hub inside the published image. Migrate first (idempotent).
# Optional first-admin bootstrap reads RETINUE_ADMIN_USERNAME / PASSWORD from
# the environment and never prints the password.
set -eu

DATA_DIR="${RETINUE_DATA_DIR:-/data}"
mkdir -p "$DATA_DIR"

python -m server.main --data-dir "$DATA_DIR" migrate

if [ -n "${RETINUE_ADMIN_USERNAME:-}" ] && [ -n "${RETINUE_ADMIN_PASSWORD:-}" ]; then
    set +e
    output=$(
        python -m server.main --data-dir "$DATA_DIR" init-admin \
            --username "$RETINUE_ADMIN_USERNAME" \
            --password "$RETINUE_ADMIN_PASSWORD" \
            --actor "${RETINUE_ADMIN_ACTOR:-$RETINUE_ADMIN_USERNAME}" 2>&1
    )
    status=$?
    set -e
    printf '%s\n' "$output"
    if [ "$status" -ne 0 ]; then
        case "$output" in
            *已存在*) ;;
            *) exit "$status" ;;
        esac
    fi
fi

if [ "$#" -eq 0 ]; then
    set -- serve --host 0.0.0.0 --port 9219
fi
exec python -m server.main --data-dir "$DATA_DIR" "$@"
