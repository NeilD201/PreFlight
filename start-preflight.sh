#!/bin/sh
# Start PCB Preflight on macOS / Linux. Run: ./start-preflight.sh [--port 8765]
set -eu
cd "$(dirname "$0")"
if [ -x ./.venv/bin/python ]; then
  PY=./.venv/bin/python
else
  PY=python3
fi
exec "$PY" ./launch_app.py "$@"
