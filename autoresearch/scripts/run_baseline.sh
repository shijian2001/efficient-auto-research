#!/usr/bin/env bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "$ROOT/scripts/env.sh"

PYTHON="$UV_PROJECT_ENVIRONMENT/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "Autoresearch environment is missing; run scripts/install.sh first" >&2
  exit 2
fi

TOKENIZER="$HOME/.cache/autoresearch/tokenizer/tokenizer.pkl"
if [ ! -f "$TOKENIZER" ]; then
  echo "Autoresearch data is missing; run scripts/prepare_data.sh first" >&2
  exit 2
fi

RUN_ID=${AUTORESEARCH_RUN_ID:-$(date +%Y%m%d_%H%M%S)}
LOG_DIR=${AUTORESEARCH_LOG_DIR:-$AUTORESEARCH_RUNTIME_ROOT/logs}
LOG_PATH="$LOG_DIR/baseline_${RUN_ID}.log"
mkdir -p "$LOG_DIR"

cd "$ROOT"
"$PYTHON" train.py 2>&1 | tee "$LOG_PATH"
