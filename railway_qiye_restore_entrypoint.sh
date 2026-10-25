#!/bin/sh
# Qiye Railway OB only. A separate service and volume are REQUIRED.
# Do not invoke upstream entrypoint.sh: it may reseed restored _app.
# Default WAIT: safe even before restoring files or copying credentials.
set -eu

case "${OMBRE_RAILWAY_RESTORE_MODE:-wait}" in
    wait)
        echo "[qiye-restore] WAIT: no OB runtime, no _app reseed"
        exec python -c 'import threading; threading.Event().wait()'
        ;;
    app)
        if [ "${OMBRE_QIYE_IDENTITY:-}" != "qiye" ] ||
           [ "${OMBRE_QIYE_RESTORE_VERIFIED:-}" != "1" ]; then
            echo "[qiye-restore] REFUSED: identity or verified restore gate missing" >&2
            exit 1
        fi
        root="${OMBRE_BUCKETS_DIR:-/app/buckets}"
        config="${OMBRE_CONFIG_PATH:-$root/config.yaml}"
        code="${OMBRE_CODE_DIR:-$root/_app}"
        if [ ! -f "$config" ] ||
           [ ! -f "$code/src/server.py" ] ||
           [ ! -f "$code/frontend/dashboard.html" ] ||
           [ ! -f "$code/VERSION" ]; then
            echo "[qiye-restore] REFUSED: restored volume incomplete" >&2
            exit 1
        fi
        if [ "$(cat "$code/VERSION")" != "3.6.13" ]; then
            echo "[qiye-restore] REFUSED: expected restored code v3.6.13" >&2
            exit 1
        fi
        echo "[qiye-restore] launching restored 3.6.13 code without image reseed"
        cd "$code" || exit 1
        exec python src/server.py
        ;;
    *)
        echo "[qiye-restore] REFUSED: unknown runtime mode" >&2
        exit 1
        ;;
esac
