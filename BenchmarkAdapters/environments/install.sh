#!/usr/bin/env bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
UV_PYTHON=${UV_INSTALLER_PYTHON:-3.11}

if [[ " $* " == *" --dry-run "* || " $* " == *" --list "* ]]; then
  PYTHON_BIN=$(uv python find "$UV_PYTHON")
  exec "$PYTHON_BIN" \
    "$ROOT/BenchmarkAdapters/environments/install.py" "$@"
fi

exec uv run --project "$ROOT/BenchmarkAdapters" --python "$UV_PYTHON" \
  "$ROOT/BenchmarkAdapters/environments/install.py" "$@"
