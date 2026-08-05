#!/usr/bin/env bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PID_FILE="$ROOT/config/proxy_bridge.pid"

if [ ! -f "$PID_FILE" ]; then
  echo "Proxy bridge is not running"
  exit 0
fi

BRIDGE_PID=$(cat "$PID_FILE")
if kill -0 "$BRIDGE_PID" 2>/dev/null && \
  tr '\0' ' ' < "/proc/$BRIDGE_PID/cmdline" 2>/dev/null | grep -Fq "$ROOT/scripts/proxy_bridge.py"; then
  kill "$BRIDGE_PID"
  wait "$BRIDGE_PID" 2>/dev/null || true
fi
unlink "$PID_FILE"
echo "Proxy bridge stopped"
