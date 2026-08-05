#!/usr/bin/env bash

set -euo pipefail

SESSION=mlebench-siim

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "SIIM preparation is not running"
  exit 0
fi

tmux send-keys -t "$SESSION" C-c
echo "Sent interrupt to $SESSION; the partial archive remains resumable"
