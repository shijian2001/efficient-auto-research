#!/usr/bin/env bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "$ROOT/scripts/env.sh"

LISTEN_HOST=$(ip -4 -o addr show docker0 | awk '{split($4, address, "/"); print address[1]}')
LISTEN_PORT=${TB2_PROXY_BRIDGE_PORT:-17893}
PROXY_URL="http://$LISTEN_HOST:$LISTEN_PORT"
BRIDGE_PID=""

cleanup() {
  if [ -n "$BRIDGE_PID" ] && kill -0 "$BRIDGE_PID" 2>/dev/null; then
    kill "$BRIDGE_PID"
    wait "$BRIDGE_PID" 2>/dev/null || true
  fi
}

trap cleanup EXIT

if ! nc -z "$LISTEN_HOST" "$LISTEN_PORT" 2>/dev/null; then
  "$ROOT/.venv/bin/python" "$ROOT/scripts/proxy_bridge.py" \
    --listen-host "$LISTEN_HOST" \
    --listen-port "$LISTEN_PORT" \
    --target-host 127.0.0.1 \
    --target-port 17892 \
    > "$ROOT/logs/oracle_smoke_proxy_bridge.log" 2>&1 &
  BRIDGE_PID=$!
  for attempt in $(seq 1 50); do
    if nc -z "$LISTEN_HOST" "$LISTEN_PORT" 2>/dev/null; then
      break
    fi
    sleep 0.1
  done
fi

harbor-local run \
  --path "$ROOT/datasets/terminal-bench-2" \
  --agent oracle \
  --include-task-name openssl-selfsigned-cert \
  --jobs-dir "$ROOT/jobs" \
  --n-attempts 1 \
  --n-concurrent 1 \
  --force-build \
  --verifier-env "HTTP_PROXY=$PROXY_URL" \
  --verifier-env "HTTPS_PROXY=$PROXY_URL" \
  --verifier-env "http_proxy=$PROXY_URL" \
  --verifier-env "https_proxy=$PROXY_URL" \
  --verifier-env "NO_PROXY=localhost,127.0.0.1" \
  --verifier-env "no_proxy=localhost,127.0.0.1"
