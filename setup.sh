#!/usr/bin/env bash
set -eu

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERROR] python3 not found. Install Python 3.10+ (on Debian: sudo apt install python3 python3-venv)." >&2
    exit 1
fi

if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    echo "[ERROR] Python 3.10 or newer is required." >&2
    exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
    echo "Creating .venv ..."
    if ! python3 -m venv .venv; then
        echo "[ERROR] Could not create .venv. On Debian, install the venv package for your Python version (for example: sudo apt install python3-venv)." >&2
        exit 1
    fi
else
    echo ".venv already exists, skip creation."
fi

echo "Installing dependencies ..."
if ! .venv/bin/python -m pip install -r requirements.txt; then
    echo "[ERROR] Dependency installation failed. Check your network connection and retry." >&2
    exit 1
fi

echo
echo "Verifying ..."
.venv/bin/python -c "import requests, PIL, ddddocr; print('deps ok')"

echo
echo "Done. Run ./run.sh for a manual check-in."
