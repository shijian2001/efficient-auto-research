#!/usr/bin/env bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "$ROOT/scripts/env.sh"

{
  echo "Step: lock Harbor dependencies"
  echo "Date: $(date --iso-8601=seconds)"
  echo "Command: uv lock --python 3.12"
  uv lock --python 3.12
} 2>&1 | tee "$ROOT/logs/01_uv_lock.log"

{
  echo "Step: create local Harbor environment"
  echo "Date: $(date --iso-8601=seconds)"
  echo "Command: uv sync --python 3.12 --locked"
  uv sync --python 3.12 --locked
} 2>&1 | tee "$ROOT/logs/02_uv_sync.log"

{
  echo "Step: verify Harbor installation"
  echo "Date: $(date --iso-8601=seconds)"
  "$ROOT/.venv/bin/python" --version
  "$ROOT/.venv/bin/harbor" --version
  "$ROOT/.venv/bin/harbor" --help
} 2>&1 | tee "$ROOT/logs/03_harbor_verify.log"
