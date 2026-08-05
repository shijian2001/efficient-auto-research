#!/usr/bin/env bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PID_FILE="$ROOT/config/proxy_bridge.pid"
LOG_FILE="$ROOT/logs/proxy_bridge_runtime.log"
LISTEN_HOST=$(ip -4 -o addr show docker0 | awk '{split($4, address, "/"); print address[1]}')
LISTEN_PORT=${TB2_PROXY_BRIDGE_PORT:-17893}

if [ -f "$PID_FILE" ]; then
  EXISTING_PID=$(cat "$PID_FILE")
  if kill -0 "$EXISTING_PID" 2>/dev/null && \
    tr '\0' ' ' < "/proc/$EXISTING_PID/cmdline" 2>/dev/null | grep -Fq "$ROOT/scripts/proxy_bridge.py"; then
    echo "Proxy bridge already running: pid=$EXISTING_PID"
    exit 0
  fi
  unlink "$PID_FILE"
fi

nohup "$ROOT/.venv/bin/python" "$ROOT/scripts/proxy_bridge.py" \
  --listen-host "$LISTEN_HOST" \
  --listen-port "$LISTEN_PORT" \
  --target-host 127.0.0.1 \
  --target-port 17892 \
  > "$LOG_FILE" 2>&1 &

BRIDGE_PID=$!
printf '%s\n' "$BRIDGE_PID" > "$PID_FILE"

for attempt in $(seq 1 50); do
  if ss -ltn | grep -q "$LISTEN_HOST:$LISTEN_PORT"; then
    echo "Proxy bridge started: pid=$BRIDGE_PID url=http://$LISTEN_HOST:$LISTEN_PORT"
    exit 0
  fi
  sleep 0.1
done

echo "Proxy bridge failed to listen; see $LOG_FILE" >&2
exit 1
