#!/usr/bin/env bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "$ROOT/scripts/env.sh"

COMPETITION=siim-isic-melanoma-classification
STATUS_FILE="$ROOT/config/siim_prepare_status.env"

write_status() {
  local status=$1
  local exit_code=${2:-}
  {
    echo "status=$status"
    echo "updated_at=$(date --iso-8601=seconds)"
    echo "competition=$COMPETITION"
    if [ -n "$exit_code" ]; then
      echo "exit_code=$exit_code"
    fi
  } > "$STATUS_FILE"
}

trap 'code=$?; write_status failed "$code"; exit "$code"' ERR
write_status running

mlebench-local prepare \
  -c "$COMPETITION" \
  --data-dir "$ROOT/data"

write_status completed 0
