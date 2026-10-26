#!/usr/bin/env bash

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -x ".venv/bin/python" ]; then
    echo "[ERROR] .venv not found. Create it first with:" >&2
    echo "  ./setup.sh" >&2
    exit 99
fi

.venv/bin/python auto_checkin.py "$@"
status=$?

echo
echo "Exit code $status: 0=ok 1=failed(check log/push) 2=out of window 3=already signed 4=no task tonight 9=crashed 99=missing deps"
exit "$status"
