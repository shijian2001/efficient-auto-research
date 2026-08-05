#!/usr/bin/env bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "$ROOT/scripts/env.sh"

{
  echo "[$(date --iso-8601=seconds)] uv lock"
  uv lock --project "$ROOT" --python 3.11
  echo
  echo "[$(date --iso-8601=seconds)] uv sync"
  uv sync --project "$ROOT" --python 3.11 --frozen
  echo
  echo "[$(date --iso-8601=seconds)] versions"
  "$ROOT/.venv/bin/python" --version
  "$ROOT/.venv/bin/python" -c 'import importlib.metadata as m; print("mlebench", m.version("mlebench")); print("tensorflow", m.version("tensorflow")); print("kaggle", m.version("kaggle"))'
  "$ROOT/.venv/bin/mlebench" --help
} 2>&1 | tee "$ROOT/logs/01_full_environment_install.log"
