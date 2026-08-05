#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

ENV_FILE="${ENV_FILE:-${REPO_ROOT}/.env}"
OUTPUT_ROOT="${OUTPUT_ROOT:-${REPO_ROOT}/examples/output}"
PAPER_MD="${PAPER_MD:-${SCRIPT_DIR}/paper.md}"
LLM_PROFILE="${LLM_PROFILE:-gpt-5.4}"
GPU_IDS="${GPU_IDS:-0}"
TIME_LIMIT="${TIME_LIMIT:-24h}"
IMAGE="${IMAGE:-aisci-paper:latest}"

cd "${REPO_ROOT}"

uv run aisci \
  --env-file "${ENV_FILE}" \
  --output-root "${OUTPUT_ROOT}" \
  paper doctor
uv run aisci \
  --env-file "${ENV_FILE}" \
  --output-root "${OUTPUT_ROOT}" \
  paper run \
  --paper-md "${PAPER_MD}" \
  --image "${IMAGE}" \
  --llm-profile "${LLM_PROFILE}" \
  --gpu-ids "${GPU_IDS}" \
  --time-limit "${TIME_LIMIT}" \
  --tui \
  --wait
