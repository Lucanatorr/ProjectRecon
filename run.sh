#!/usr/bin/env bash
# Splice launcher (macOS / Linux). Creates the venv on first run, installs
# dependencies, then starts the app.
#
#   ./run.sh            start the app
#   ./run.sh --test     run the test suite instead
#   ./run.sh --update   reinstall dependencies first
set -euo pipefail
cd "$(dirname "$0")"

PYTHON=".venv/bin/python"
MODE="run"
PORT="${PORT:-8501}"
UPDATE=0

for arg in "$@"; do
  case "$arg" in
    --test)   MODE="test" ;;
    --update) UPDATE=1 ;;
    --port=*) PORT="${arg#*=}" ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [ ! -x "$PYTHON" ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
  UPDATE=1
fi

if [ "$UPDATE" -eq 1 ]; then
  echo "Installing dependencies..."
  "$PYTHON" -m pip install --upgrade pip --quiet
  "$PYTHON" -m pip install -r requirements.txt --quiet
fi

if [ "$MODE" = "test" ]; then
  exec "$PYTHON" -m pytest -q
fi

echo "Starting Splice on http://localhost:${PORT}"
exec "$PYTHON" -m streamlit run app.py --server.port "$PORT"
