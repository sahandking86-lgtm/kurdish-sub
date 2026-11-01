#!/usr/bin/env bash
# ============================================================
#  Kurdish Sub — start script for Linux / macOS
#  First run installs everything (a few minutes, one time)
# ============================================================
set -e
cd "$(dirname "$0")"

PY_BIN=""
for c in python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1; then PY_BIN="$c"; break; fi
done
if [ -z "$PY_BIN" ]; then
  echo "Python 3.10+ is required. Install it with:  sudo apt install python3 python3-venv python3-pip"
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Note: the system ffmpeg was not found — Kurdish Sub will use its bundled copy"
  echo "      (pip install imageio-ffmpeg, already in requirements.txt)."
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "Creating the local environment (one time)…"
  "$PY_BIN" -m venv .venv || {
    echo "Could not create the venv. On Debian/Ubuntu: sudo apt install python3-venv"
    exit 1
  }
fi

VPY=".venv/bin/python"

if [ ! -f ".venv/.installed" ]; then
  echo "Installing the engines (this can take a few minutes)…"
  "$VPY" -m pip install --upgrade pip --quiet
  "$VPY" -m pip install -r requirements.txt
  date > ".venv/.installed"
fi

echo "Starting Kurdish Sub…"
exec "$VPY" -m app "$@"
