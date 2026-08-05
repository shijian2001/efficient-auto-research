#!/usr/bin/env bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
{
  "$ROOT/.venv/bin/python" "$ROOT/scripts/verify_installation.py"
  echo
  "$ROOT/scripts/show_status.sh"
} 2>&1 | tee "$ROOT/logs/07_installation_verification.log"
