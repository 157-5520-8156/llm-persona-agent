#!/usr/bin/env bash
set -euo pipefail

LIVE_ROOT="/Users/geoff/Projects/Girl-Agent"
WT="/Users/geoff/Projects/Girl-Agent/.claude/worktrees/fix-cost-optimization"
cd "$LIVE_ROOT"
export PATH="/Users/geoff/homebrew/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export DATABASE_PATH="$LIVE_ROOT/data/companion.epoch2.sqlite"
export PYTHONPATH="$WT/src${PYTHONPATH:+:$PYTHONPATH}"

exec "$WT/.venv/bin/python" -m uvicorn companion_daemon.app:app --host 127.0.0.1 --port 8765
