#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

ENV_FILE="${ENV_FILE:-${REPO_ROOT}/.env}"
OUTPUT_ROOT="${OUTPUT_ROOT:-${REPO_ROOT}/examples/output}"
COMPETITION_NAME="${COMPETITION_NAME:-detecting-insults-in-social-commentary}"
COMPETITION_ZIP="${COMPETITION_ZIP:-${SCRIPT_DIR}/detecting-insults-in-social-commentary.zip}"
LLM_PROFILE="${LLM_PROFILE:-gpt-5.4}"
GPU_IDS="${GPU_IDS:-0}"
TIME_LIMIT="${TIME_LIMIT:-12h}"
IMAGE="${IMAGE:-aisci-mle:test}"

# --name is the canonical competition slug; keep it explicit if the zip filename ever differs.

cd "${REPO_ROOT}"

uv run aisci \
  --env-file "${ENV_FILE}" \
  --output-root "${OUTPUT_ROOT}" \
  mle doctor
uv run aisci \
  --env-file "${ENV_FILE}" \
  --output-root "${OUTPUT_ROOT}" \
  mle run \
  --name "${COMPETITION_NAME}" \
  --zip "${COMPETITION_ZIP}" \
  --image "${IMAGE}" \
  --llm-profile "${LLM_PROFILE}" \
  --gpu-ids "${GPU_IDS}" \
  --time-limit "${TIME_LIMIT}" \
  --tui \
  --wait
