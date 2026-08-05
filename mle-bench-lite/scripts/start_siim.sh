#!/usr/bin/env bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SESSION=mlebench-siim

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "SIIM preparation is already running in tmux session: $SESSION"
  exit 0
fi

tmux new-session -d -s "$SESSION" \
  "cd '$ROOT' && ./scripts/prepare_siim.sh > logs/06_prepare_siim-isic-melanoma-classification.log 2>&1"
echo "Started SIIM preparation in tmux session: $SESSION"
