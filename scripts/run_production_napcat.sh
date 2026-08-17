#!/usr/bin/env bash
set -euo pipefail

LIVE_ROOT="/Users/geoff/Projects/Girl-Agent"
WT="/Users/geoff/Projects/Girl-Agent/.claude/worktrees/fix-cost-optimization"
cd "$LIVE_ROOT"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export DATABASE_PATH="$LIVE_ROOT/data/companion.epoch2.sqlite"
export PYTHONPATH="$WT/src${PYTHONPATH:+:$PYTHONPATH}"
# cwd is LIVE_ROOT (relative configs/*), but production code/YAML live in the
# worktree. Pin character.yaml absolutely so a stale main-repo copy cannot win.
export CHARACTER_PATH="$WT/configs/character.yaml"
: "${QQ_TURN_OBSERVATION_PATH:=data/private/qq-turns.jsonl}"
export QQ_TURN_OBSERVATION_PATH
: "${QQ_MESSAGE_BATCH_SECONDS:=0.8}"
export QQ_MESSAGE_BATCH_SECONDS

exec "$WT/.venv/bin/python" -m companion_daemon.napcat_cli --adapter napcat --host 127.0.0.1 --port 8787
