#!/usr/bin/env bash
# Usage: bash scripts/mle/run_single_task.sh <competition-id> <mle-data-root> [server-id] [-- arbor args]
set -euo pipefail

COMPETITION_ID=${1:?competition id is required}
DATA_ROOT=${2:?MLE-Bench data root is required}
if [ "$#" -ge 3 ] && [[ "$3" =~ ^[0-9]+$ ]]; then
  SERVER_ID=$3
  shift 3
else
  SERVER_ID=111
  shift 2
fi
if [ "${1:-}" = "--" ]; then
  shift
fi

BASE_PORT=${ARBOR_MLE_BASE_PORT:-5005}
PORT=$((BASE_PORT + SERVER_ID))
PUBLIC_DIR="${DATA_ROOT}/${COMPETITION_ID}/prepared/public"
DESC_FILE="${PUBLIC_DIR}/description.md"
RUN_ROOT=${ARBOR_MLE_RUN_ROOT:-"$(pwd)/runs"}
RUN_DIR=${ARBOR_MLE_RUN_DIR:-"${RUN_ROOT}/$(date +%Y%m%d_%H%M%S)_${COMPETITION_ID}"}
mkdir -p "$RUN_DIR"

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ARBOR_REPO_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
ARBOR_PYTHON=${ARBOR_PYTHON:-python}
MLEBENCH_PYTHON=${MLEBENCH_PYTHON:-python}
PYTHONPATH_ROOT="$RUN_DIR/pythonpath"
mkdir -p "$PYTHONPATH_ROOT"
ln -s "$ARBOR_REPO_ROOT/src" "$PYTHONPATH_ROOT/arbor"
MLEBENCH_BIN=$(cd "$(dirname "$MLEBENCH_PYTHON")" && pwd)

PYTHONPATH="$PYTHONPATH_ROOT" "$MLEBENCH_PYTHON" -m arbor.mle.format_server \
  --data-root "$DATA_ROOT" \
  --competition-id "$COMPETITION_ID" \
  --port "$PORT" >"$RUN_DIR/format_server.log" 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT INT TERM

SERVER_READY=0
for _ in $(seq 1 60); do
  if "$MLEBENCH_PYTHON" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${PORT}/health', timeout=1).read()" >/dev/null 2>&1; then
    SERVER_READY=1
    break
  fi
  sleep 0.5
done
if [ "$SERVER_READY" -ne 1 ]; then
  echo "format validation server failed to start; see $RUN_DIR/format_server.log" >&2
  exit 2
fi

PATH="$MLEBENCH_BIN:$PATH" PYTHONPATH="$PYTHONPATH_ROOT" "$ARBOR_PYTHON" -m arbor.mle.run \
  --competition-id "$COMPETITION_ID" \
  --data-dir "$PUBLIC_DIR" \
  --desc-file "$DESC_FILE" \
  --run-dir "$RUN_DIR" \
  --validation-url "http://127.0.0.1:${PORT}" \
  -- "$@"
