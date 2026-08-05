#!/usr/bin/env bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "$ROOT/scripts/env.sh"

: "${TB2_AGENT:?Set TB2_AGENT to module.path:ClassName or a built-in Harbor agent name}"

TB2_MODEL=${TB2_MODEL:-}
TB2_ATTEMPTS=${TB2_ATTEMPTS:-1}
TB2_CONCURRENCY=${TB2_CONCURRENCY:-1}
TB2_INCLUDE_TASK=${TB2_INCLUDE_TASK:-}
TB2_FORCE_BUILD=${TB2_FORCE_BUILD:-1}
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

COMMAND=(
  "$ROOT/.venv/bin/harbor" run
  --path "$ROOT/datasets/terminal-bench-2"
  --agent "$TB2_AGENT"
  --jobs-dir "$ROOT/jobs"
  --n-attempts "$TB2_ATTEMPTS"
  --n-concurrent "$TB2_CONCURRENCY"
)

if [ -n "$TB2_MODEL" ]; then
  COMMAND+=(--model "$TB2_MODEL")
fi

if [ -n "$TB2_INCLUDE_TASK" ]; then
  COMMAND+=(--include-task-name "$TB2_INCLUDE_TASK")
fi

if [ "$TB2_FORCE_BUILD" = "1" ]; then
  COMMAND+=(--force-build)
fi

if nc -z 127.0.0.1 17892 2>/dev/null; then
  if ! nc -z "$LISTEN_HOST" "$LISTEN_PORT" 2>/dev/null; then
    "$ROOT/.venv/bin/python" "$ROOT/scripts/proxy_bridge.py" \
      --listen-host "$LISTEN_HOST" \
      --listen-port "$LISTEN_PORT" \
      --target-host 127.0.0.1 \
      --target-port 17892 \
      > "$ROOT/logs/custom_agent_proxy_bridge.log" 2>&1 &
    BRIDGE_PID=$!
    for attempt in $(seq 1 50); do
      if nc -z "$LISTEN_HOST" "$LISTEN_PORT" 2>/dev/null; then
        break
      fi
      sleep 0.1
    done
  fi

  if ! nc -z "$LISTEN_HOST" "$LISTEN_PORT" 2>/dev/null; then
    echo "Could not expose the host proxy to task containers" >&2
    exit 1
  fi

  for key in HTTP_PROXY HTTPS_PROXY http_proxy https_proxy; do
    COMMAND+=(--agent-env "$key=$PROXY_URL")
    COMMAND+=(--verifier-env "$key=$PROXY_URL")
  done
  COMMAND+=(--agent-env "NO_PROXY=localhost,127.0.0.1")
  COMMAND+=(--agent-env "no_proxy=localhost,127.0.0.1")
  COMMAND+=(--verifier-env "NO_PROXY=localhost,127.0.0.1")
  COMMAND+=(--verifier-env "no_proxy=localhost,127.0.0.1")
fi

HOME="$ROOT/home" \
XDG_CACHE_HOME="$ROOT/.cache" \
HARBOR_VIEWER_JOBS_DIR="$ROOT/jobs" \
CODEX_AUTH_JSON_PATH="$CODEX_AUTH_JSON_PATH" \
PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" \
  "${COMMAND[@]}"
