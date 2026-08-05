#!/bin/bash
set -euo pipefail

RUN_TAG=${1:?usage: watch_and_plot_run.sh <run_tag>}
ROOT=/mnt/sdc/shijianwang/efficient-agent-research
LOG_ROOT="$ROOT/run-logs/$RUN_TAG"
PLOT_LOG="$LOG_ROOT/plot_watcher.log"

mkdir -p "$LOG_ROOT"
{
  echo "[$(date -Is)] watcher started for $RUN_TAG"
  while true; do
    running=$(docker ps --format '{{.Names}}' | grep -E 'mle-(efficient-auto-research|MLEvolve)-' || true)
    if [ -z "$running" ]; then
      break
    fi
    echo "[$(date -Is)] still running:"
    echo "$running"
    sleep 300
  done

  echo "[$(date -Is)] containers finished, plotting"
  "$ROOT/../miniconda3/envs/mlebench/bin/python" "$ROOT/docker-eval/plot_agent_score_comparison.py" \
    --run-tag "$RUN_TAG" \
    --out "$ROOT/docker-eval/plots/${RUN_TAG}_hourly_agent_scores.png" \
    --summary "$ROOT/docker-eval/plots/${RUN_TAG}_hourly_agent_scores.json"
  echo "[$(date -Is)] plotting done"
} >> "$PLOT_LOG" 2>&1
