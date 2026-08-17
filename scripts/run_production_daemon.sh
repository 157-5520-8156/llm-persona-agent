#!/usr/bin/env bash
set -euo pipefail

# One root: see the note in run_production_napcat.sh.
LIVE_ROOT="/Users/geoff/Projects/Girl-Agent"
cd "$LIVE_ROOT"
export PATH="/Users/geoff/homebrew/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export DATABASE_PATH="$LIVE_ROOT/data/companion.epoch2.sqlite"
export PYTHONPATH="$LIVE_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export CHARACTER_PATH="$LIVE_ROOT/configs/character.yaml"

exec "$LIVE_ROOT/.venv/bin/python" -m uvicorn companion_daemon.app:app --host 127.0.0.1 --port 8765
