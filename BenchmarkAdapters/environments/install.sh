#!/usr/bin/env bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
UV_PYTHON=${UV_INSTALLER_PYTHON:-3.11}
PYTHON_BIN=${UV_INSTALLER_PYTHON_BIN:-}

if [ -z "$PYTHON_BIN" ]; then
  PYTHON_BIN=$(uv python find --no-python-downloads "$UV_PYTHON" 2>/dev/null || true)
fi
if [ -z "$PYTHON_BIN" ]; then
  PYTHON_BIN=$(command -v python3 || true)
fi
if [ -z "$PYTHON_BIN" ]; then
  echo "Python 3.11 or 3.12 is required to run the environment installer" >&2
  exit 2
fi

"$PYTHON_BIN" - <<'PY'
import sys

if not ((3, 11) <= sys.version_info[:2] < (3, 13)):
    raise SystemExit(
        f"environment installer requires Python >=3.11,<3.13; found {sys.version.split()[0]}"
    )
PY

exec "$PYTHON_BIN" "$ROOT/BenchmarkAdapters/environments/install.py" "$@"
