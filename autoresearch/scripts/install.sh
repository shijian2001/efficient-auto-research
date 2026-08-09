#!/usr/bin/env bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "$ROOT/scripts/env.sh"

command -v uv >/dev/null 2>&1 || {
  echo "uv is not installed or not on PATH" >&2
  exit 2
}

uv python install 3.10 --install-dir "$UV_PYTHON_INSTALL_DIR"
MANAGED_PYTHON=$(uv python find --managed-python --no-project 3.10)

uv sync \
  --project "$ROOT" \
  --python "$MANAGED_PYTHON" \
  --locked

"$UV_PROJECT_ENVIRONMENT/bin/python" - <<'PY'
from pathlib import Path
import importlib.metadata as metadata
import sysconfig

header = Path(sysconfig.get_paths()["include"]) / "Python.h"
if not header.is_file():
    raise SystemExit(f"managed Python header is missing: {header}")
print(f"python_header={header}")
for package in ("torch", "triton", "numpy", "pyarrow", "rustbpe", "kernels"):
    print(f"{package}={metadata.version(package)}")
PY
