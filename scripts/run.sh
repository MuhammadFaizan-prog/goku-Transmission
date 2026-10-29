#!/usr/bin/env bash
# Goku Transmission - launcher for macOS / Linux
set -e
cd "$(dirname "$0")/.."
if [ ! -d .venv ]; then
  python3 -m venv .venv
  . .venv/bin/activate
  pip install --upgrade pip >/dev/null
  pip install -r requirements.txt
else
  . .venv/bin/activate
fi
python -m goku_transmission "$@"
