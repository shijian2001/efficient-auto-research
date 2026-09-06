#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
set -a
source "${MLE_RELAY_ENV_FILE:-$HOME/.mle_relay_env}"
set +a
export LLM_UPSTREAM_PROXY=http://127.0.0.1:17892
export LLM_FORCE_MODEL=gpt-5.6-terra
export LLM_FORCE_PARAMETERS_JSON='{"temperature":1.0,"reasoning_effort":"high"}'
export LLM_UPSTREAM_TIMEOUT=600 LLM_MAX_RETRIES=20
export LLM_PROXY_API_KEY="${UPSTREAM_API_KEY:?set UPSTREAM_API_KEY in the relay environment file}"
export LLM_PROXY_AGENT_NAME=mle-host-relay-proxied
export LLM_TOKEN_LOG_PATH="${LLM_TOKEN_LOG_PATH:-$ROOT/cache/mle-host-relay-proxied.tokens.jsonl}"
"$ROOT/BenchmarkAdapters/.venv/bin/python" -c 'from BenchmarkAdapters.MLEBenchLite.network import check_proxy; check_proxy()'
exec "$ROOT/BenchmarkAdapters/.venv/bin/python" -m BenchmarkAdapters.LLMRelay.server --host 127.0.0.1 --port 6201
