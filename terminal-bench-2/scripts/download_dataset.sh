#!/usr/bin/env bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "$ROOT/scripts/env.sh"

DATASET_DIR="$ROOT/datasets/terminal-bench-2"
TASK_COUNT=$(find "$DATASET_DIR" -name task.toml 2>/dev/null | wc -l)

if [ "$TASK_COUNT" -eq 89 ]; then
  echo "Terminal-Bench 2.0 is already complete: 89 tasks"
  exit 0
fi

GIT_CONFIG_COUNT=1 \
GIT_CONFIG_KEY_0=http.version \
GIT_CONFIG_VALUE_0=HTTP/1.1 \
  harbor-local download terminal-bench@2.0 \
    --output-dir "$ROOT/datasets" \
    --export
