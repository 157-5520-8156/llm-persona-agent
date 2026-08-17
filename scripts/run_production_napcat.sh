#!/usr/bin/env bash
set -euo pipefail

# The cost-optimization branch is checked out in the main repository, so code,
# venv, and character YAML all resolve from one root.  The previous split root
# (a git worktree under .claude/worktrees) left launchd pointing at a directory
# that could be removed while the daemon was still running.
LIVE_ROOT="/Users/geoff/Projects/Girl-Agent"
cd "$LIVE_ROOT"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export DATABASE_PATH="$LIVE_ROOT/data/companion.epoch2.sqlite"
export PYTHONPATH="$LIVE_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export CHARACTER_PATH="$LIVE_ROOT/configs/character.yaml"
: "${QQ_TURN_OBSERVATION_PATH:=data/private/qq-turns.jsonl}"
export QQ_TURN_OBSERVATION_PATH
: "${QQ_MESSAGE_BATCH_SECONDS:=0.8}"
export QQ_MESSAGE_BATCH_SECONDS

exec "$LIVE_ROOT/.venv/bin/python" -m companion_daemon.napcat_cli --adapter napcat --host 127.0.0.1 --port 8787
