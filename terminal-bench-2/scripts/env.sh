#!/usr/bin/env bash

set -euo pipefail

TB2_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
export TB2_ROOT
TB2_HOST_HOME=${HOME}
export TB2_HOST_HOME
export VIRTUAL_ENV="$TB2_ROOT/.venv"
export PATH="$VIRTUAL_ENV/bin:$PATH"
export XDG_CACHE_HOME="$TB2_ROOT/.cache"
export UV_CACHE_DIR="$TB2_ROOT/.uv-cache"
export HARBOR_VIEWER_JOBS_DIR="$TB2_ROOT/jobs"
export CODEX_AUTH_JSON_PATH="${CODEX_AUTH_JSON_PATH:-$TB2_HOST_HOME/.codex/auth.json}"

harbor-local() {
  HOME="$TB2_ROOT/home" \
  XDG_CACHE_HOME="$TB2_ROOT/.cache" \
  HARBOR_VIEWER_JOBS_DIR="$TB2_ROOT/jobs" \
  CODEX_AUTH_JSON_PATH="$CODEX_AUTH_JSON_PATH" \
    "$TB2_ROOT/.venv/bin/harbor" "$@"
}

export -f harbor-local
