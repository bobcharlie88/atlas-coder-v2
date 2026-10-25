#!/usr/bin/env bash
# ATLAS launcher (macOS / Linux)
set -e
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python)
[ -z "$PY" ] && { echo "Python 3.10+ required."; exit 1; }
if [ ! -d .venv ]; then echo "Creating virtual environment..."; "$PY" -m venv .venv; fi
. .venv/bin/activate
pip install -q --disable-pip-version-check -r requirements.txt
python app.py "$@"
