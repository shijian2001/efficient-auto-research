#!/usr/bin/env bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
LOG_FILE="$ROOT/logs/23_complete_installation_verification.log"

{
  echo "[$(date --iso-8601=seconds)] Revalidating dataset"
  "$ROOT/scripts/validate_dataset.sh"
  echo
  echo "[$(date --iso-8601=seconds)] Verifying installation"
  "$ROOT/.venv/bin/python" "$ROOT/scripts/verify_installation.py"
  echo
  echo "[$(date --iso-8601=seconds)] Installation size"
  du -sh "$ROOT"
  echo
  echo "[$(date --iso-8601=seconds)] Filesystem capacity"
  df -h "$ROOT" /var/lib/docker
} 2>&1 | tee "$LOG_FILE"
