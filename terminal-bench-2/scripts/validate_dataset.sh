#!/usr/bin/env bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "$ROOT/scripts/env.sh"

{
  echo "Step: validate Terminal-Bench 2.0 dataset"
  echo "Date: $(date --iso-8601=seconds)"
  "$ROOT/.venv/bin/python" "$ROOT/scripts/validate_dataset.py" \
    "$ROOT/datasets/terminal-bench-2" \
    "$ROOT/config/dataset_manifest.json"
} 2>&1 | tee "$ROOT/logs/11_dataset_validation.log"
