#!/usr/bin/env bash

set -euo pipefail

MLE_LITE_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
export MLE_LITE_ROOT
export MLE_BENCH_SOURCE="$MLE_LITE_ROOT/source"
export MLE_BENCH_DATA_DIR="$MLE_LITE_ROOT/data"
export VIRTUAL_ENV="$MLE_LITE_ROOT/.venv"
export PATH="$VIRTUAL_ENV/bin:$PATH"
export XDG_CACHE_HOME="$MLE_LITE_ROOT/.cache"
export UV_CACHE_DIR="$MLE_LITE_ROOT/.uv-cache"
export TMPDIR="$MLE_LITE_ROOT/tmp"
export KAGGLE_CONFIG_DIR="${KAGGLE_CONFIG_DIR:-$HOME/.kaggle}"

export HTTP_PROXY="${HTTP_PROXY:-http://127.0.0.1:17892}"
export HTTPS_PROXY="${HTTPS_PROXY:-http://127.0.0.1:17892}"
export http_proxy="${http_proxy:-$HTTP_PROXY}"
export https_proxy="${https_proxy:-$HTTPS_PROXY}"
export NO_PROXY="${NO_PROXY:-localhost,127.0.0.1,::1}"
export no_proxy="${no_proxy:-$NO_PROXY}"

mlebench-local() {
  "$MLE_LITE_ROOT/.venv/bin/mlebench" "$@"
}

export -f mlebench-local
