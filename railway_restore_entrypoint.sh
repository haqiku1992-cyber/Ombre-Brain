#!/bin/sh
# Railway-only restoration wrapper for the official Ombre Brain 3.6.14 image.
# Deliberately bypasses /app/entrypoint.sh to prevent its _app reseed logic.
# The default remains WAIT even if Railway supplies a startCommand argument.
set -eu

case "${OMBRE_RAILWAY_RESTORE_MODE:-wait}" in
    wait)
        echo "[railway-restore] WAIT: OB not started; persistent data unchanged"
        exec python -c 'import threading; threading.Event().wait()'
        ;;
    app)
        restore_config="${OMBRE_CONFIG_PATH:-/app/buckets/config.yaml}"
        restore_code="${OMBRE_CODE_DIR:-/app/buckets/_app}"
        if [ ! -f "$restore_config" ] ||
           [ ! -f "$restore_code/VERSION" ] ||
           [ ! -f "$restore_code/src/server.py" ] ||
           [ ! -f "$restore_code/frontend/dashboard.html" ]; then
            echo "[railway-restore] REFUSED: restored data/code incomplete" >&2
            exit 1
        fi
        restored_version="$(cat "$restore_code/VERSION")"
        if [ "$restored_version" != "3.6.14" ]; then
            echo "[railway-restore] REFUSED: expected restored code v3.6.14" >&2
            exit 1
        fi
        echo "[railway-restore] launching restored v3.6.14 code without reseed"
        cd "$restore_code" || exit 1
        exec python src/server.py
        ;;
    *)
        echo "[railway-restore] REFUSED: unknown OMBRE_RAILWAY_RESTORE_MODE" >&2
        exit 1
        ;;
esac
