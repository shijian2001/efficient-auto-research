#!/usr/bin/env bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "$ROOT/scripts/env.sh"

PYTHON="$UV_PROJECT_ENVIRONMENT/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "Autoresearch environment is missing; run scripts/install.sh first" >&2
  exit 2
fi

ARGS=()
if [ "${AUTORESEARCH_REQUIRE_CUDA:-0}" = "1" ]; then
  ARGS+=(--require-cuda)
fi
exec "$PYTHON" "$ROOT/scripts/verify_installation.py" "${ARGS[@]}"
