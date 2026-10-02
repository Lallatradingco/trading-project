#!/usr/bin/env bash
# One-command start for the standalone pattern portal (macOS / Linux).
# Creates its own virtualenv, does the first download + scan if needed,
# then serves on http://127.0.0.1:8765 (or $PATTERN_PORTAL_PORT).
set -euo pipefail
cd "$(dirname "$0")/.."
VENV=.venv-pattern-portal
if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q -r pattern_portal/requirements.txt
fi
PY="$VENV/bin/python"
if [ ! -f pattern_data/scan/current/meta.json ]; then
  echo "First run: downloading price history and scanning (universe: ${UNIVERSE:-nifty500})"
  "$PY" -m pattern_portal setup --universe "${UNIVERSE:-nifty500}"
fi
PORT="${PATTERN_PORTAL_PORT:-8765}"
( sleep 2; command -v open >/dev/null && open "http://127.0.0.1:$PORT" ) &
exec "$PY" -m pattern_portal serve --port "$PORT"
