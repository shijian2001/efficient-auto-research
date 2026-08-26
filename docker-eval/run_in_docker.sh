#!/bin/bash
# 在 Docker 容器中跑单个 agent (sudo-free GPU + 宿主代理)
# 支持: efficient-auto-research / Arbor / MLEvolve
#
# 关键设计：
#   - 复用每个 Adapter 的锁定 UV 环境（只读挂载），不构建重镜像
#   - GPU: --device 手动挂 nvidia 设备 + 宿主 libcuda（无需 nvidia-container-toolkit / sudo）
#   - 固定单卡: 只挂 /dev/nvidia${GPU_ID} → 容器内 nvidia0 + CUDA_VISIBLE_DEVICES=0
#   - 网络: Docker bridge 仅访问宿主 relay/format 服务；模型下载按运行时代理配置
#   - HF 在线: 挂宿主 HF cache(可写) + HF_HUB_OFFLINE=0 + NO_PROXY 不含 HF,经 Clash 下模型(XLM-R 等多语言模型 chaii 必需)
#   - 路径一致挂载（容器内路径 == 宿主路径），避免 pip editable 包路径错位
#
#   - LLM 走仓库内统一中继 (BenchmarkAdapters/LLMRelay/server.py)：
#       agent → http://127.0.0.1:$PROXY_PORT/v1 → explicitly configured relay/model track
#     模型重写/reasoning_effort/参数清洗/重试/非流式化/tool兜底/token记录 全在代理内。
#     端口按 GPU_ID 错开 (host 网络下多容器共享网络栈，不能撞)。
#
# 用法: bash run_in_docker.sh <agent> <competition> <gpu_id> [max_steps] [timeout_sec]
# EAR_CLI_MODE=g3_legacy_mle_knowledge enables the exact MLEvolve cold-start bundle.

set -eu

SCRIPT_PATH=$(readlink -f "$0")
CONTAINER_IMAGE=${CONTAINER_IMAGE:-ubuntu:20.04}

AGENT=${1:?用法: run_in_docker.sh <agent> <competition> <gpu_id> [steps] [timeout]}
COMP=${2:?competition id}
GPU_ID=${3:-0}
STEPS=${4:-2}
TIMEOUT=${5:-900}
RUN_TAG=${RUN_TAG:-$(date +%Y%m%d_%H%M%S)}
SEED=${SEED:-0}
EXTRA_DOCKER_FLAGS=()
BENCHMARK_MOUNTS=()
HOST_FORMAT_SERVER_PID=""
HOST_RELAY_PID=""
HOST_DOWNLOAD_PROXY_PID=""

cleanup_host_services() {
  if [ -n "$HOST_FORMAT_SERVER_PID" ]; then
    kill "$HOST_FORMAT_SERVER_PID" 2>/dev/null || true
  fi
  if [ -n "$HOST_RELAY_PID" ]; then
    kill "$HOST_RELAY_PID" 2>/dev/null || true
  fi
  if [ -n "$HOST_DOWNLOAD_PROXY_PID" ]; then
    kill "$HOST_DOWNLOAD_PROXY_PID" 2>/dev/null || true
  fi
}
trap cleanup_host_services EXIT INT TERM

archive_tracked_source() {
  source_dir=$(readlink -f "$1")
  destination=$2
  commit=$3
  git_root=$(git -C "$source_dir" rev-parse --show-toplevel)
  git_root=$(readlink -f "$git_root")
  if [ "$git_root" = "$source_dir" ]; then
    git -C "$source_dir" archive "$commit" | tar -x -C "$destination"
    return
  fi
  source_relative=$(realpath --relative-to="$git_root" "$source_dir")
  git -C "$git_root" archive "$commit:$source_relative" | tar -x -C "$destination"
}

add_uv_runtime_mounts() {
  uv_venv=$(readlink -f "$1")
  uv_python=$uv_venv/bin/python
  test -x "$uv_python" || { echo "UV runtime is not installed: $uv_python" >&2; exit 2; }
  BENCHMARK_MOUNTS+=( -v "${uv_venv}:${uv_venv}:ro" )
  if [ -L "$uv_python" ]; then
    uv_python_link=$(readlink "$uv_python")
    case "$uv_python_link" in
      /*) ;;
      *) uv_python_link=$uv_venv/bin/$uv_python_link ;;
    esac
    uv_python_link_home=$(dirname "$(dirname "$uv_python_link")")
    uv_python_home=$(dirname "$(dirname "$(readlink -f "$uv_python")")")
    if [ "$uv_python_link_home" != "$uv_venv" ]; then
      BENCHMARK_MOUNTS+=( -v "${uv_python_link_home}:${uv_python_link_home}:ro" )
    fi
    if [ "$uv_python_home" != "$uv_venv" ] && [ "$uv_python_home" != "$uv_python_link_home" ]; then
      BENCHMARK_MOUNTS+=( -v "${uv_python_home}:${uv_python_home}:ro" )
    fi
  fi
}

# nvidia-smi's logical GPU index is not guaranteed to equal the Linux device
# minor on this host. Resolve the requested physical GPU before constructing
# the manual --device mount, otherwise e.g. logical GPU 0 may expose another
# physical card inside the container.
GPU_UUID=$(nvidia-smi --query-gpu=uuid --format=csv,noheader -i "$GPU_ID" | tr -d '[:space:]')
GPU_MINOR=$(nvidia-smi -q -i "$GPU_ID" | awk -F ':' '/Minor Number/ { gsub(/[[:space:]]/, "", $2); print $2; exit }')
case "$GPU_MINOR" in
  ''|*[!0-9]*)
    echo "无法解析逻辑 GPU $GPU_ID 的 device minor" >&2
    exit 2
    ;;
esac
GPU_DEVICE=/dev/nvidia${GPU_MINOR}
if [ ! -c "$GPU_DEVICE" ]; then
  echo "GPU device 不存在或不是字符设备: $GPU_DEVICE" >&2
  exit 2
fi

# 单步执行超时不再由 launcher 强加：各 agent 用自己发布的默认
# (MLEvolve exec.timeout=32400 上游默认；EAR min(3600, timeout//3))。
# 冒烟测试需要快速失败时可用 MLE_EXEC_TIMEOUT 覆盖 MLEvolve。
MLE_EXEC_TIMEOUT=${MLE_EXEC_TIMEOUT:-}

CLASH_PROXY=${CLASH_PROXY:-http://127.0.0.1:17892}
LLM_UPSTREAM_PROXY=${LLM_UPSTREAM_PROXY-$CLASH_PROXY}
EAR=${EAR_ROOT:-$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd)}
ROOT=${HOST_ROOT:-$(dirname "$EAR")}
ALIGNED_KNOWLEDGE_ROOT=${ALIGNED_KNOWLEDGE_ROOT:-${EAR}/ear-worktrees/g3-mlevolve-knowledge-aligned/configs/mlevolve_coldstart}

# --- LLM 上游 (代理转发目标) ---
UPSTREAM_BASE_URL=${UPSTREAM_BASE_URL:-${LLM_BASE_URL:-}}
if [ -z "$UPSTREAM_BASE_URL" ]; then
  echo "缺少显式 UPSTREAM_BASE_URL" >&2
  exit 2
fi
UPSTREAM_API_KEY=${UPSTREAM_API_KEY:-${OPENAI_API_KEY:-}}
if [ -z "$UPSTREAM_API_KEY" ]; then
  echo "缺少上游 API credential：请设置 UPSTREAM_API_KEY 或 OPENAI_API_KEY" >&2
  exit 2
fi
export UPSTREAM_API_KEY
# Only the host relay receives this credential. Agent containers receive the
# literal placeholder `proxy` below.
MODEL=${MODEL:-}
if [ -z "$MODEL" ]; then
  echo "缺少显式 MODEL" >&2
  exit 2
fi
LLM_FORCE_PARAMETERS_JSON=${LLM_FORCE_PARAMETERS_JSON:-}
if [ -z "$LLM_FORCE_PARAMETERS_JSON" ]; then
  echo "缺少显式 LLM_FORCE_PARAMETERS_JSON" >&2
  exit 2
fi
LLM_UPSTREAM_TIMEOUT=${LLM_UPSTREAM_TIMEOUT:-}   # 空 = 不限
LLM_MAX_RETRIES=${LLM_MAX_RETRIES:-20}
LLM_MAX_UPSTREAM_CALLS=${LLM_MAX_UPSTREAM_CALLS:-}
LLM_SKIP_UPSTREAM_READY=${LLM_SKIP_UPSTREAM_READY:-0}

# --- 本地转发代理 ---
PROXY_PORT=$((6200 + GPU_ID))
DOCKER_BRIDGE_IP=${DOCKER_BRIDGE_IP:-$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}')}
RELAY_BIND_HOST=${RELAY_BIND_HOST:-$DOCKER_BRIDGE_IP}
RELAY_CONTAINER_HOST=${RELAY_CONTAINER_HOST:-host.docker.internal}
PROXY_URL="http://${RELAY_CONTAINER_HOST}:${PROXY_PORT}/v1"
RELAY_API_KEY=${RELAY_API_KEY:-$(openssl rand -hex 32)}
CONTAINER_HTTP_PROXY=$CLASH_PROXY

MLE_BENCH_DATA_ROOT=${MLE_BENCH_DATA_ROOT:-${EAR}/mle-bench-data}
case "$MLE_BENCH_DATA_ROOT" in
  *[!A-Za-z0-9_./-]*)
    echo "Unsafe MLE_BENCH_DATA_ROOT path: $MLE_BENCH_DATA_ROOT" >&2
    exit 2
    ;;
esac
DATA=${MLE_BENCH_DATA_ROOT}/${COMP}/prepared/public
test -d "$DATA" || { echo "Missing public MLE-Bench data: $DATA" >&2; exit 95; }
HF_CACHE_HOST=${HF_CACHE_HOST:-${MLE_RUN_ROOT}/cache/huggingface}
MLE_CACHE_HOST=${MLE_CACHE_HOST:-${MLE_RUN_ROOT}/cache/mle-bench}
TOKEN_LOG_DIR=${MLE_RUN_ROOT}/relay-telemetry
TOKEN_LOG_PATH=${TOKEN_LOG_DIR}/${AGENT}_${COMP}_gpu${GPU_ID}.jsonl
mkdir -p "$TOKEN_LOG_DIR"
mkdir -p "$HF_CACHE_HOST" "$MLE_CACHE_HOST"

# Each Agent branch populates an explicit mount allowlist. Never mount the
# repository or MLE-Bench data root wholesale into an Agent container.
BENCHMARK_MOUNTS=()

# grading server 端口按 GPU_ID 错开（host 网络下三容器共享网络栈，不能撞）
GRADING_PORT=$((5200 + GPU_ID))

# CPU 配额：与 MLEvolve 官方 run_single_task.sh 的 CPUS_PER_TASK=21 对齐。
# 每容器独占 21 核、按 GPU_ID 错开区间（8 卡 × 21 = 168 ≤ 256），杜绝互踩；
# 所有 agent 同配额保证硬件公平。
CPUS_PER_TASK=${CPUS_PER_TASK:-21}
CPU_START=$((GPU_ID * CPUS_PER_TASK))
CPUSET="${CPU_START}-$((CPU_START + CPUS_PER_TASK - 1))"

CONTAINER_NAME=${CONTAINER_NAME:-"mle-${AGENT}-${COMP}-gpu${GPU_ID}"}
docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

# The real upstream credential remains in this host-owned relay process. Agent
# containers receive only OPENAI_API_KEY=proxy.
HOST_RELAY_LOG=$TOKEN_LOG_DIR/relay_${AGENT}_${COMP}_gpu${GPU_ID}.log
RELAY_PYTHON=${RELAY_PYTHON:-${EAR}/mle-bench-lite/.venv/bin/python}
test -x "$RELAY_PYTHON" || { echo "Host relay Python is not installed: $RELAY_PYTHON" >&2; exit 2; }
# Do not impose a model-output cap from legacy campaign configs. Keep the
# effective model track in the launch manifest aligned with the relay.
LLM_FORCE_PARAMETERS_JSON=$(
  LLM_FORCE_PARAMETERS_JSON="$LLM_FORCE_PARAMETERS_JSON" "$RELAY_PYTHON" - <<'PY'
import json
import os

parameters = json.loads(os.environ["LLM_FORCE_PARAMETERS_JSON"])
if not isinstance(parameters, dict):
    raise SystemExit("LLM_FORCE_PARAMETERS_JSON must be a JSON object")
for field in ("max_output_tokens", "max_completion_tokens", "max_tokens"):
    parameters.pop(field, None)
parameters["temperature"] = 1.0
print(json.dumps(parameters, sort_keys=True, separators=(",", ":")))
PY
)
test -n "$LLM_FORCE_PARAMETERS_JSON" || { echo "Model parameters became empty after normalization" >&2; exit 2; }
case "$CLASH_PROXY" in
  http://127.0.0.1:*|http://localhost:*)
    CLASH_PROXY_PORT=${CLASH_PROXY##*:}
    case "$CLASH_PROXY_PORT" in
      ''|*[!0-9]*) echo "Unsupported local HTTP proxy URL: $CLASH_PROXY" >&2; exit 2 ;;
    esac
    DOWNLOAD_PROXY_PORT=$((17892 + GPU_ID))
    "$RELAY_PYTHON" -u "$EAR/BenchmarkAdapters/tcp_forwarder.py" \
      --listen-host "$DOCKER_BRIDGE_IP" --listen-port "$DOWNLOAD_PROXY_PORT" \
      --target-host 127.0.0.1 --target-port "$CLASH_PROXY_PORT" \
      >"$TOKEN_LOG_DIR/download_proxy_${GPU_ID}.log" 2>&1 &
    HOST_DOWNLOAD_PROXY_PID=$!
    CONTAINER_HTTP_PROXY="http://${RELAY_CONTAINER_HOST}:${DOWNLOAD_PROXY_PORT}"
    DOWNLOAD_PROXY_READY=0
    for _ in $(seq 1 50); do
      if ! kill -0 "$HOST_DOWNLOAD_PROXY_PID" 2>/dev/null; then
        break
      fi
      if "$RELAY_PYTHON" -c "import socket; socket.create_connection(('$DOCKER_BRIDGE_IP',$DOWNLOAD_PROXY_PORT),1).close()" 2>/dev/null; then
        DOWNLOAD_PROXY_READY=1
        break
      fi
      sleep 0.1
    done
    if [ "$DOWNLOAD_PROXY_READY" -ne 1 ]; then
      echo "Download proxy failed to start; see $TOKEN_LOG_DIR/download_proxy_${GPU_ID}.log" >&2
      exit 2
    fi
    ;;
esac
UPSTREAM_BASE_URL=$UPSTREAM_BASE_URL UPSTREAM_API_KEY=$UPSTREAM_API_KEY \
LLM_UPSTREAM_PROXY=$LLM_UPSTREAM_PROXY \
LLM_FORCE_MODEL=$MODEL LLM_FORCE_PARAMETERS_JSON=$LLM_FORCE_PARAMETERS_JSON \
LLM_UPSTREAM_TIMEOUT=$LLM_UPSTREAM_TIMEOUT LLM_MAX_RETRIES=$LLM_MAX_RETRIES \
LLM_MAX_UPSTREAM_CALLS=$LLM_MAX_UPSTREAM_CALLS \
LLM_TOKEN_LOG_PATH=$TOKEN_LOG_PATH LLM_PROXY_AGENT_NAME=$AGENT \
LLM_PROXY_API_KEY=$RELAY_API_KEY \
  "$RELAY_PYTHON" -u "$EAR/BenchmarkAdapters/LLMRelay/server.py" --host "$RELAY_BIND_HOST" --port "$PROXY_PORT" \
  >"$HOST_RELAY_LOG" 2>&1 &
HOST_RELAY_PID=$!
for _ in $(seq 1 100); do
  if ! kill -0 "$HOST_RELAY_PID" 2>/dev/null; then
    echo "Host LLM relay exited early; see $HOST_RELAY_LOG" >&2
    exit 1
  fi
  "$RELAY_PYTHON" -c "import urllib.request; opener=urllib.request.build_opener(urllib.request.ProxyHandler({})); opener.open('http://$RELAY_BIND_HOST:$PROXY_PORT/health', timeout=1)" 2>/dev/null && break
  sleep 0.1
done
if [ "$LLM_SKIP_UPSTREAM_READY" != "1" ]; then
  RELAY_PROBE_HOST=$RELAY_BIND_HOST RELAY_PROBE_PORT=$PROXY_PORT \
  RELAY_PROBE_MODEL=$MODEL RELAY_PROBE_KEY=$RELAY_API_KEY \
  "$RELAY_PYTHON" - <<'PY' || { echo "LLM upstream unavailable; see $HOST_RELAY_LOG" >&2; exit 1; }
import json
import os
import urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
request = urllib.request.Request(
    f"http://{os.environ['RELAY_PROBE_HOST']}:{os.environ['RELAY_PROBE_PORT']}/v1/chat/completions",
    data=json.dumps({
        'model': os.environ['RELAY_PROBE_MODEL'],
        'max_tokens': 8,
        'messages': [{'role': 'user', 'content': 'Reply READY'}],
    }).encode(),
    headers={
        'Authorization': f"Bearer {os.environ['RELAY_PROBE_KEY']}",
        'Content-Type': 'application/json',
    },
)
with opener.open(request, timeout=120) as response:
    if response.status != 200:
        raise SystemExit(response.status)
PY
fi

# 各 agent 在容器内的启动命令
case "$AGENT" in
  efficient-auto-research)
    # 版本迭代: EAR_AGENT_DIR 可指向某个 worktree (见 efficient-auto-research/docs/ITERATION.md)，
    # 默认跑主工作目录。容器只接收当前 commit 的干净快照，不挂载整个 EAR。
    EAR_AGENT_DIR=${EAR_AGENT_DIR:-${EAR}/mle-bench-agents/$AGENT}
    EAR_MLE_UV_VENV=${EAR_MLE_UV_VENV:-${EAR}/BenchmarkAdapters/environments/mle/ear/.venv}
    EAR_PYTHON=${EAR_PYTHON:-${EAR_MLE_UV_VENV}/bin/python}
    AGENT_PYTHON=$EAR_PYTHON
    EAR_CLI_MODE=${EAR_CLI_MODE:-current}
    if [ "$EAR_CLI_MODE" = "g3_legacy" ] || [ "$EAR_CLI_MODE" = "g3_legacy_knowledge" ] || [ "$EAR_CLI_MODE" = "g3_legacy_mle_knowledge" ]; then
      # Reproduce the frozen G3 CLI exactly. G3 predates the run-id, seed,
      # provenance, and multi-root flags used by later EAR iterations, so its
      # experiment identity is carried by the isolated output path and the
      # host launch manifest instead of injecting unsupported agent flags.
      EAR_KNOWLEDGE_ARGS=""
      if [ "$EAR_CLI_MODE" = "g3_legacy_knowledge" ]; then
        EAR_KNOWLEDGE_PATH=${EAR_KNOWLEDGE_PATH:-$EAR_AGENT_DIR/configs/coldstart_knowledge.json}
        EAR_KNOWLEDGE_TOP_K=${EAR_KNOWLEDGE_TOP_K:-2}
        case "$EAR_KNOWLEDGE_PATH" in
          *[!A-Za-z0-9_./-]*)
            echo "拒绝不安全的 cold-start knowledge 路径: $EAR_KNOWLEDGE_PATH" >&2
            exit 2
            ;;
        esac
        if [ ! -f "$EAR_KNOWLEDGE_PATH" ]; then
          echo "G3 Knowledge 文件不存在: $EAR_KNOWLEDGE_PATH" >&2
          exit 2
        fi
        EAR_KNOWLEDGE_PATH=$(readlink -f "$EAR_KNOWLEDGE_PATH")
        EAR_KNOWLEDGE_SHA256=$(sha256sum "$EAR_KNOWLEDGE_PATH" | awk '{print $1}')
        echo "=== G3 cold-start knowledge: $EAR_KNOWLEDGE_PATH (sha256=$EAR_KNOWLEDGE_SHA256, top_k=$EAR_KNOWLEDGE_TOP_K) ==="
        EAR_KNOWLEDGE_ARGS="--knowledge-path '$EAR_KNOWLEDGE_PATH' --knowledge-top-k $EAR_KNOWLEDGE_TOP_K"
      fi
      if [ "$EAR_CLI_MODE" = "g3_legacy_mle_knowledge" ]; then
        EAR_KNOWLEDGE_TASK_MAP=${EAR_KNOWLEDGE_TASK_MAP:-$ALIGNED_KNOWLEDGE_ROOT/competition_tag_classified.json}
        EAR_KNOWLEDGE_MODEL_MAP=${EAR_KNOWLEDGE_MODEL_MAP:-$ALIGNED_KNOWLEDGE_ROOT/models_guidance_classified.json}
        for knowledge_path in "$EAR_KNOWLEDGE_TASK_MAP" "$EAR_KNOWLEDGE_MODEL_MAP"; do
          case "$knowledge_path" in
            *[!A-Za-z0-9_./-]*)
              echo "拒绝不安全的 MLEvolve cold-start knowledge 路径: $knowledge_path" >&2
              exit 2
              ;;
          esac
          if [ ! -f "$knowledge_path" ]; then
            echo "MLEvolve 对齐知识文件不存在: $knowledge_path" >&2
            exit 2
          fi
        done
        EAR_KNOWLEDGE_TASK_MAP=$(readlink -f "$EAR_KNOWLEDGE_TASK_MAP")
        EAR_KNOWLEDGE_MODEL_MAP=$(readlink -f "$EAR_KNOWLEDGE_MODEL_MAP")
        EAR_KNOWLEDGE_TASK_SHA256=$(sha256sum "$EAR_KNOWLEDGE_TASK_MAP" | awk '{print $1}')
        EAR_KNOWLEDGE_MODEL_SHA256=$(sha256sum "$EAR_KNOWLEDGE_MODEL_MAP" | awk '{print $1}')
        echo "=== G3 MLEvolve-aligned cold-start: task_map=$EAR_KNOWLEDGE_TASK_MAP (sha256=$EAR_KNOWLEDGE_TASK_SHA256) ==="
        echo "=== G3 MLEvolve-aligned cold-start: model_map=$EAR_KNOWLEDGE_MODEL_MAP (sha256=$EAR_KNOWLEDGE_MODEL_SHA256) ==="
        EAR_KNOWLEDGE_ARGS="--competition-id '$COMP' --knowledge-task-map '$EAR_KNOWLEDGE_TASK_MAP' --knowledge-model-map '$EAR_KNOWLEDGE_MODEL_MAP'"
      fi
      EAR_SOURCE_COMMIT=$(git -C "$EAR_AGENT_DIR" rev-parse HEAD)
      EAR_SOURCE_BRANCH=$(git -C "$EAR_AGENT_DIR" rev-parse --abbrev-ref HEAD)
      EAR_SOURCE_DIRTY=$(test -n "$(git -C "$EAR_AGENT_DIR" status --porcelain --untracked-files=no -- .)" && echo true || echo false)
      EAR_OUTPUT_DIR=${EAR_OUTPUT_DIR:-$EAR_AGENT_DIR/docker_runs/${RUN_TAG}_$COMP}
      echo "=== EAR G3 legacy CLI: $EAR_AGENT_DIR ==="
      echo "=== EAR 宿主 provenance: $EAR_SOURCE_BRANCH @ $EAR_SOURCE_COMMIT (dirty=$EAR_SOURCE_DIRTY) ==="
      INNER_CMD="
        AGENT_DIR=$EAR_AGENT_DIR
        OUT='$EAR_OUTPUT_DIR'; mkdir -p \$OUT
        cd \$AGENT_DIR
        '$EAR_PYTHON' -u agent/run.py --data_dir $DATA --desc_file $DATA/description.md \\
          --output \$OUT/submission.csv --max_steps $STEPS --timeout $TIMEOUT --model $MODEL $EAR_KNOWLEDGE_ARGS
      "
    else
    # G7 intentionally follows the G3 reward path: candidate-reported local
    # METRIC guides search; official MLE-bench grading happens only outside the
    # run after the final submission is hash-verified.
    EAR_SEARCH_REWARD="candidate_reported_metric"
    EAR_ISOLATION_LEVEL="unrestricted-subprocess_allowlisted-filesystem"
    EAR_INITIAL_ROOT_ATTEMPTS=${EAR_INITIAL_ROOT_ATTEMPTS:-3}
    EAR_NEW_ROOT_STAGNATION=${EAR_NEW_ROOT_STAGNATION:-8}
    EAR_NEW_ROOT_COOLDOWN_ATTEMPTS=${EAR_NEW_ROOT_COOLDOWN_ATTEMPTS:-4}
    EAR_REWARD_MODE=${EAR_REWARD_MODE:-self_reported_compat}
    EAR_SOURCE_COMMIT=$(git -C "$EAR_AGENT_DIR" rev-parse HEAD)
    EAR_SOURCE_BRANCH=$(git -C "$EAR_AGENT_DIR" rev-parse --abbrev-ref HEAD)
    EAR_SOURCE_DIRTY=$(test -n "$(git -C "$EAR_AGENT_DIR" status --porcelain --untracked-files=no -- .)" && echo true || echo false)
    EAR_OUTPUT_DIR=${EAR_OUTPUT_DIR:-$EAR_AGENT_DIR/docker_runs/${RUN_TAG}_$COMP}
    mkdir -p "$EAR_OUTPUT_DIR"
    LAUNCHER_SHA256=$(sha256sum "$SCRIPT_PATH" | awk '{print $1}')
    RELAY_SHA256=$(sha256sum "${EAR}/BenchmarkAdapters/LLMRelay/server.py" | awk '{print $1}')
    LAUNCH_MANIFEST=$EAR_OUTPUT_DIR/launch_manifest.json
    LAUNCH_MANIFEST_TMP=$(mktemp "${LAUNCH_MANIFEST}.tmp.XXXXXX")
    MANIFEST_COMP="$COMP" MANIFEST_RUN_ID="$RUN_TAG" MANIFEST_SEED="$SEED" \
    MANIFEST_MODEL="$MODEL" MANIFEST_MODEL_PARAMETERS="$LLM_FORCE_PARAMETERS_JSON" \
    MANIFEST_STEPS="$STEPS" MANIFEST_TIMEOUT="$TIMEOUT" \
    MANIFEST_GPU="$GPU_ID" MANIFEST_GPU_UUID="$GPU_UUID" \
    MANIFEST_GPU_MINOR="$GPU_MINOR" MANIFEST_CPUSET="$CPUSET" \
    MANIFEST_CPU_COUNT="$CPUS_PER_TASK" MANIFEST_DATA="$DATA" \
    MANIFEST_AGENT_COMMIT="$EAR_SOURCE_COMMIT" \
    MANIFEST_AGENT_BRANCH="$EAR_SOURCE_BRANCH" \
    MANIFEST_AGENT_DIRTY="$EAR_SOURCE_DIRTY" \
    MANIFEST_LAUNCHER_SHA256="$LAUNCHER_SHA256" \
    MANIFEST_RELAY_SHA256="$RELAY_SHA256" \
    MANIFEST_CONTAINER_IMAGE="$CONTAINER_IMAGE" \
    MANIFEST_ISOLATION_LEVEL="$EAR_ISOLATION_LEVEL" \
    MANIFEST_SEARCH_REWARD="$EAR_SEARCH_REWARD" \
    MANIFEST_INITIAL_ROOT_ATTEMPTS="$EAR_INITIAL_ROOT_ATTEMPTS" \
    MANIFEST_NEW_ROOT_STAGNATION="$EAR_NEW_ROOT_STAGNATION" \
    MANIFEST_NEW_ROOT_COOLDOWN_ATTEMPTS="$EAR_NEW_ROOT_COOLDOWN_ATTEMPTS" \
      ${EAR}/mle-bench-lite/.venv/bin/python - "$LAUNCH_MANIFEST_TMP" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

payload = {
    "schema_version": 2,
    "created_at": datetime.now(timezone.utc).isoformat(),
    "competition": os.environ["MANIFEST_COMP"],
    "run_id": os.environ["MANIFEST_RUN_ID"],
    "seed": int(os.environ["MANIFEST_SEED"]),
    "model": os.environ["MANIFEST_MODEL"],
    "model_parameters": json.loads(os.environ["MANIFEST_MODEL_PARAMETERS"]),
    "steps": int(os.environ["MANIFEST_STEPS"]),
    "timeout_seconds": int(os.environ["MANIFEST_TIMEOUT"]),
    "hardware": {
        "host_gpu_id": int(os.environ["MANIFEST_GPU"]),
        "host_gpu_uuid": os.environ["MANIFEST_GPU_UUID"],
        "host_gpu_minor": int(os.environ["MANIFEST_GPU_MINOR"]),
        "container_cuda_visible_devices": "0",
        "cpuset_cpus": os.environ["MANIFEST_CPUSET"],
        "cpu_count": int(os.environ["MANIFEST_CPU_COUNT"]),
    },
    "data_path": os.environ["MANIFEST_DATA"],
    "agent": {
        "commit": os.environ["MANIFEST_AGENT_COMMIT"],
        "branch": os.environ["MANIFEST_AGENT_BRANCH"],
        "dirty": os.environ["MANIFEST_AGENT_DIRTY"] == "true",
    },
    "launcher_sha256": os.environ["MANIFEST_LAUNCHER_SHA256"],
    "relay_sha256": os.environ["MANIFEST_RELAY_SHA256"],
    "container_image": os.environ["MANIFEST_CONTAINER_IMAGE"],
    "search_reward": os.environ["MANIFEST_SEARCH_REWARD"],
    "search_policy": {
        "initial_root_attempts": int(os.environ["MANIFEST_INITIAL_ROOT_ATTEMPTS"]),
        "new_root_stagnation": int(os.environ["MANIFEST_NEW_ROOT_STAGNATION"]),
        "new_root_cooldown_attempts": int(
            os.environ["MANIFEST_NEW_ROOT_COOLDOWN_ATTEMPTS"]
        ),
    },
    "official_grading": "outer_post_run_mlebench",
    "isolation_level": os.environ["MANIFEST_ISOLATION_LEVEL"],
}
# mktemp has already created this unique path; write into that file and then
# publish it atomically with the hard-link below.
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(payload, handle, sort_keys=True, indent=2)
    handle.write("\n")
PY
    if ! ln "$LAUNCH_MANIFEST_TMP" "$LAUNCH_MANIFEST"; then
      rm -f "$LAUNCH_MANIFEST_TMP"
      echo "launch manifest 已存在，拒绝覆盖同一 run: $LAUNCH_MANIFEST" >&2
      exit 2
    fi
    rm -f "$LAUNCH_MANIFEST_TMP"
    LAUNCH_MANIFEST_SHA256=$(sha256sum "$LAUNCH_MANIFEST" | awk '{print $1}')
    echo "=== EAR 代码目录: $EAR_AGENT_DIR ==="
    echo "=== EAR 宿主 provenance: $EAR_SOURCE_BRANCH @ $EAR_SOURCE_COMMIT (dirty=$EAR_SOURCE_DIRTY) ==="
    EAR_RUN_HELP=$("$EAR_PYTHON" "$EAR_AGENT_DIR/agent/run.py" --help 2>&1)
    EAR_OPTIONAL_ARGS=""
    if printf '%s' "$EAR_RUN_HELP" | grep -q -- '--initial-root-attempts'; then
      EAR_OPTIONAL_ARGS="$EAR_OPTIONAL_ARGS --initial-root-attempts $EAR_INITIAL_ROOT_ATTEMPTS"
    fi
    if printf '%s' "$EAR_RUN_HELP" | grep -q -- '--new-root-stagnation'; then
      EAR_OPTIONAL_ARGS="$EAR_OPTIONAL_ARGS --new-root-stagnation $EAR_NEW_ROOT_STAGNATION"
    fi
    if printf '%s' "$EAR_RUN_HELP" | grep -q -- '--new-root-cooldown-attempts'; then
      EAR_OPTIONAL_ARGS="$EAR_OPTIONAL_ARGS --new-root-cooldown-attempts $EAR_NEW_ROOT_COOLDOWN_ATTEMPTS"
    fi
    if printf '%s' "$EAR_RUN_HELP" | grep -q -- '--reward-mode'; then
      EAR_OPTIONAL_ARGS="$EAR_OPTIONAL_ARGS --reward-mode $EAR_REWARD_MODE"
    fi
    INNER_CMD="
      AGENT_DIR=$EAR_AGENT_DIR
      OUT='$EAR_OUTPUT_DIR'; mkdir -p \$OUT
      cd \$AGENT_DIR
      '$EAR_PYTHON' -u agent/run.py --data_dir $DATA --desc_file $DATA/description.md \
        --output \$OUT/submission.csv --max_steps $STEPS --timeout $TIMEOUT --model $MODEL \
        --temperature 1.0 \
        --seed $SEED --run-id $RUN_TAG \
        --launch-manifest-sha256 $LAUNCH_MANIFEST_SHA256 \
        --source-commit $EAR_SOURCE_COMMIT --source-branch $EAR_SOURCE_BRANCH \
        --source-dirty $EAR_SOURCE_DIRTY --require-source-provenance \
        $EAR_OPTIONAL_ARGS
    "
    fi

    if [ "$EAR_SOURCE_DIRTY" = "true" ]; then
      echo "EAR 隔离运行要求源码 clean；请提交、暂存到独立 commit，或清理工作区后重试: $EAR_AGENT_DIR" >&2
      exit 2
    fi

    # Build a tracked-files-only snapshot. Mounting EAR_AGENT_DIR directly would
    # also expose docker_runs/ from previous experiments, so use git archive and
    # mount the snapshot read-only instead.
    EAR_SOURCE_SNAPSHOT_ROOT=${EAR_SOURCE_SNAPSHOT_ROOT:-${EAR}/cache/ear-source-snapshots}
    mkdir -p "$EAR_SOURCE_SNAPSHOT_ROOT"
    EAR_SOURCE_SNAPSHOT=$(mktemp -d "${EAR_SOURCE_SNAPSHOT_ROOT}/${RUN_TAG}_${COMP}.XXXXXX")
    archive_tracked_source "$EAR_AGENT_DIR" "$EAR_SOURCE_SNAPSHOT" "$EAR_SOURCE_COMMIT"

    EAR_OUTPUT_REL=${EAR_OUTPUT_DIR#"$EAR_AGENT_DIR"/}
    if [ "$EAR_OUTPUT_REL" != "$EAR_OUTPUT_DIR" ]; then
      mkdir -p "$EAR_SOURCE_SNAPSHOT/$EAR_OUTPUT_REL"
    fi

    BENCHMARK_MOUNTS=(
      -v "${EAR_SOURCE_SNAPSHOT}:${EAR_AGENT_DIR}:ro"
      -v "${EAR_OUTPUT_DIR}:${EAR_OUTPUT_DIR}:rw"
      -v "${DATA}:${DATA}:ro"
    )
    add_uv_runtime_mounts "$EAR_MLE_UV_VENV"

    # Optional knowledge files outside the clean agent snapshot are mounted
    # individually, never by exposing their parent worktree or repository.
    if [ "$EAR_CLI_MODE" = "g3_legacy_knowledge" ]; then
      case "$EAR_KNOWLEDGE_PATH" in
        "$EAR_AGENT_DIR"/*) ;;
        *) BENCHMARK_MOUNTS+=( -v "${EAR_KNOWLEDGE_PATH}:${EAR_KNOWLEDGE_PATH}:ro" ) ;;
      esac
    fi
    if [ "$EAR_CLI_MODE" = "g3_legacy_mle_knowledge" ]; then
      BENCHMARK_MOUNTS+=(
        -v "${EAR_KNOWLEDGE_TASK_MAP}:${EAR_KNOWLEDGE_TASK_MAP}:ro"
        -v "${EAR_KNOWLEDGE_MODEL_MAP}:${EAR_KNOWLEDGE_MODEL_MAP}:ro"
      )
    fi

    EAR_ISOLATION_CHECKS="
      test -r '$DATA/description.md' || { echo 'EAR isolation failed: public data is unavailable' >&2; exit 96; }
      test ! -e '${MLE_BENCH_DATA_ROOT}/${COMP}/prepared/private' || { echo 'EAR isolation failed: private data is visible' >&2; exit 97; }
      test ! -e '/root/.cache/mle-bench/data/${COMP}/prepared/private' || { echo 'EAR isolation failed: cached private data is visible' >&2; exit 98; }
      test ! -e '${EAR}/baselines/MLEvolve' || { echo 'EAR isolation failed: MLEvolve is visible' >&2; exit 99; }
      test ! -e '${EAR}/mle-bench-research' || { echo 'EAR isolation failed: research reports are visible' >&2; exit 100; }
      if find '$EAR_AGENT_DIR/docker_runs' -mindepth 1 -maxdepth 1 ! -name '${RUN_TAG}_${COMP}' -print -quit | grep -q .; then
        echo 'EAR isolation failed: prior EAR runs are visible' >&2
        exit 101
      fi
    "
    INNER_CMD="$EAR_ISOLATION_CHECKS
$INNER_CMD"
    echo "=== EAR 隔离源码快照: $EAR_SOURCE_SNAPSHOT ==="
    ;;
  Arbor)
    # Arbor follows MLEvolve's low-intrusion integration model: MLE-Bench is
    # unchanged and supplies prepared data plus an external format validator.
    # The agent container sees only the public task directory.
    # Arbor is vendored beneath this benchmark repository and may itself have
    # a nested .git directory. Freeze the outer benchmark commit explicitly so
    # that the adapter source cannot be shadowed by that inner repository.
    ARBOR_SOURCE_REPO=${ARBOR_SOURCE_REPO:-$EAR/baselines/Arbor-longrun-patched}
    ARBOR_SOURCE_SUBTREE=${ARBOR_SOURCE_SUBTREE:-.}
    ARBOR_SOURCE_ALLOW_DIRTY=${ARBOR_SOURCE_ALLOW_DIRTY:-1}
    ARBOR_SOURCE_DIR=${ARBOR_SOURCE_REPO}/${ARBOR_SOURCE_SUBTREE}
    ARBOR_OUTPUT_DIR=${ARBOR_OUTPUT_DIR:-${EAR}/run-logs/${RUN_TAG}_Arbor_${COMP}_gpu${GPU_ID}}
    mkdir -p "$ARBOR_OUTPUT_DIR"
    if ! git -C "$ARBOR_SOURCE_REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
      echo "Arbor source repository not found: $ARBOR_SOURCE_REPO" >&2
      exit 2
    fi
    if [ ! -d "$ARBOR_SOURCE_DIR" ]; then
      echo "Arbor source subtree not found: $ARBOR_SOURCE_DIR" >&2
      exit 2
    fi
    if [ -n "$(git -C "$ARBOR_SOURCE_REPO" status --porcelain -- "$ARBOR_SOURCE_SUBTREE")" ]; then
      echo "Arbor isolation requires a clean source subtree: $ARBOR_SOURCE_REPO/$ARBOR_SOURCE_SUBTREE" >&2
      exit 2
    fi
    ARBOR_SOURCE_COMMIT=$(git -C "$ARBOR_SOURCE_REPO" rev-parse HEAD)
    ARBOR_SOURCE_SNAPSHOT_ROOT=${ARBOR_SOURCE_SNAPSHOT_ROOT:-${EAR}/cache/arbor-source-snapshots}
    mkdir -p "$ARBOR_SOURCE_SNAPSHOT_ROOT"
    ARBOR_SOURCE_SNAPSHOT=$(mktemp -d "${ARBOR_SOURCE_SNAPSHOT_ROOT}/${RUN_TAG}_${COMP}.XXXXXX")
    archive_tracked_source "$ARBOR_SOURCE_DIR" "$ARBOR_SOURCE_SNAPSHOT" "$ARBOR_SOURCE_COMMIT"
    for required_adapter_file in src/mle/run.py src/mle/eval_runner.py src/mle/adapter.py src/mle/state_store.py; do
      if [ ! -f "$ARBOR_SOURCE_SNAPSHOT/$required_adapter_file" ]; then
        echo "Arbor archived commit is missing adapter file: $required_adapter_file" >&2
        exit 2
      fi
    done

    ARBOR_PYTHONPATH_ROOT="$ARBOR_OUTPUT_DIR/pythonpath"
    mkdir -p "$ARBOR_PYTHONPATH_ROOT"
    ln -s "$ARBOR_SOURCE_SNAPSHOT/src" "$ARBOR_PYTHONPATH_ROOT/arbor"
    ARBOR_MLE_UV_VENV=${ARBOR_MLE_UV_VENV:-${EAR}/BenchmarkAdapters/environments/mle/arbor/.venv}
    MLEBENCH_PYTHON=${MLEBENCH_PYTHON:-${ARBOR_MLE_UV_VENV}/bin/python}
    ARBOR_PYTHON=${ARBOR_PYTHON:-${ARBOR_MLE_UV_VENV}/bin/python}
    AGENT_PYTHON=$ARBOR_PYTHON
    if [ ! -x "$ARBOR_PYTHON" ]; then
      echo "Required Arbor MLE UV runtime is not installed: $ARBOR_PYTHON" >&2
      exit 2
    fi
    ARBOR_METRIC_DIRECTION=$(PYTHONPATH="$ARBOR_PYTHONPATH_ROOT" "$MLEBENCH_PYTHON" -c \
      "from pathlib import Path; from arbor.mle.common import infer_metric_direction; print(infer_metric_direction('$COMP', Path('$DATA')))" )
    ARBOR_TIME_BUDGET=$((TIMEOUT > 300 ? TIMEOUT - 120 : TIMEOUT))

    PYTHONPATH="$ARBOR_PYTHONPATH_ROOT" "$MLEBENCH_PYTHON" -u -m arbor.mle.format_server \
      --data-root "${MLE_BENCH_DATA_ROOT}" --competition-id "$COMP" \
      --host "$DOCKER_BRIDGE_IP" --port "$GRADING_PORT" \
      >"$ARBOR_OUTPUT_DIR/format_server.log" 2>&1 &
    HOST_FORMAT_SERVER_PID=$!
    FORMAT_READY=0
    for _ in $(seq 1 60); do
      if curl -fsS "http://${DOCKER_BRIDGE_IP}:${GRADING_PORT}/health" >/dev/null 2>&1; then
        FORMAT_READY=1
        break
      fi
      sleep 0.5
    done
    if [ "$FORMAT_READY" -ne 1 ]; then
      echo "Arbor format server failed; see $ARBOR_OUTPUT_DIR/format_server.log" >&2
      exit 2
    fi

    BENCHMARK_MOUNTS=(
      -v "${ARBOR_SOURCE_SNAPSHOT}:${ARBOR_SOURCE_SNAPSHOT}:ro"
      -v "${ARBOR_OUTPUT_DIR}:${ARBOR_OUTPUT_DIR}:rw"
      -v "${DATA}:${DATA}:ro"
      -v "${EAR}/BenchmarkAdapters/tcp_forwarder.py:${EAR}/BenchmarkAdapters/tcp_forwarder.py:ro"
    )
    add_uv_runtime_mounts "$ARBOR_MLE_UV_VENV"
    ARBOR_ISOLATION_CHECKS="
      test -r '$DATA/description.md' || { echo 'Arbor isolation failed: public data is unavailable' >&2; exit 96; }
      test ! -e '${MLE_BENCH_DATA_ROOT}/${COMP}/prepared/private' || { echo 'Arbor isolation failed: private data is visible' >&2; exit 97; }
      test ! -e '/private/data' || { echo 'Arbor isolation failed: /private/data is visible' >&2; exit 98; }
      test ! -e '${EAR}/baselines/MLEvolve' || { echo 'Arbor isolation failed: MLEvolve is visible' >&2; exit 99; }
      test ! -e '${EAR}/mle-bench-research' || { echo 'Arbor isolation failed: research reports are visible' >&2; exit 100; }
    "
    INNER_CMD="$ARBOR_ISOLATION_CHECKS
      export PATH='$ARBOR_MLE_UV_VENV/bin':\$PATH
      export PYTHONPATH='$ARBOR_PYTHONPATH_ROOT'
      '$ARBOR_PYTHON' '${EAR}/BenchmarkAdapters/tcp_forwarder.py' \
        --listen-port '$GRADING_PORT' --target-host '$RELAY_CONTAINER_HOST' --target-port '$GRADING_PORT' &
      FORMAT_FORWARDER_PID=\$!
      trap 'kill \$FORMAT_FORWARDER_PID 2>/dev/null || true' EXIT
      timeout --foreground --signal=TERM --kill-after=10s $TIMEOUT '$ARBOR_PYTHON' -m arbor.mle.run \\
        --competition-id '$COMP' \\
        --data-dir '$DATA' \\
        --desc-file '$DATA/description.md' \\
        --run-dir '$ARBOR_OUTPUT_DIR' \\
        --validation-url 'http://127.0.0.1:$GRADING_PORT' \\
        --metric-direction '$ARBOR_METRIC_DIRECTION' \\
        --time-budget '$ARBOR_TIME_BUDGET' \\
        --provider openai-chat --model '$MODEL' --base-url '$PROXY_URL' \\
        --run-name '${RUN_TAG}_${COMP}' --force \\
        -- --max-cycles '$STEPS'
    "
    echo "=== Arbor source snapshot: $ARBOR_SOURCE_SNAPSHOT ($ARBOR_SOURCE_COMMIT) ==="
    echo "=== Arbor metric direction: $ARBOR_METRIC_DIRECTION ==="
    echo "=== Arbor internal time budget: ${ARBOR_TIME_BUDGET}s (outer timeout: ${TIMEOUT}s) ==="
    ;;
  MLEvolve)
    # 对齐上游官方 run_single_task.sh：除必要的路径/LLM 端点/时长外一律用上游默认
    # (global_memory/parallel_search/debug_depth/initial_drafts 均不覆盖)。
    #
    # 唯一的能力性偏离: coldstart.use_coldstart=False (显式关闭)。
    # 原因: coldstart 的 competition_tag_classified.json 硬编码了 MLE-bench 全部
    # 75 题的"竞赛→任务类别"映射, 属于针对本 benchmark 的预计算适配 (运行时应由
    # agent 自己读题判断)。对照实验里保留它对无知识库的 EAR 不公平。
    # 当前研究协议只保留无 cold-start 的纯原版 baseline，不提供运行时开关。
    #
    # exp_name 必须是三段式 (result_parse_agent 用 split('_')[2] 取 exp_id 做 format 校验)
    # config.__init__ 会自动加 "YYYYmmdd_HHMMSS_" 前缀 → 传入 $COMP 即可满足三段式。
    # 跑完后照官方脚本做 submission fusion (top solutions 集成)。
    MLE_AGENT_DIR=${MLE_AGENT_DIR:-${EAR}/baselines/MLEvolve}
    # Optional per-run root keeps MLEvolve journals, workspaces, and fusion
    # artifacts isolated. Unset callers retain the historical ./runs layout.
    MLE_RUN_ROOT=${MLE_RUN_ROOT:-$MLE_AGENT_DIR/runs}
    mkdir -p "$MLE_RUN_ROOT"
    MLE_SESSION_ROOT=$MLE_RUN_ROOT/session_${RUN_TAG}_${COMP}
    if [ -e "$MLE_SESSION_ROOT" ]; then
      echo "MLEvolve session output already exists: $MLE_SESSION_ROOT" >&2
      exit 2
    fi
    mkdir -p "$MLE_SESSION_ROOT"
    if ! git -C "$MLE_AGENT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
      echo "MLEvolve repository not found: $MLE_AGENT_DIR" >&2
      exit 2
    fi
    if [ -n "$(git -C "$MLE_AGENT_DIR" status --porcelain --untracked-files=no -- .)" ]; then
      echo "MLEvolve isolation requires clean tracked source: $MLE_AGENT_DIR" >&2
      exit 2
    fi
    MLE_SOURCE_COMMIT=$(git -C "$MLE_AGENT_DIR" rev-parse HEAD)
    MLE_SOURCE_SNAPSHOT_ROOT=${MLE_SOURCE_SNAPSHOT_ROOT:-${EAR}/cache/mlevolve-source-snapshots}
    mkdir -p "$MLE_SOURCE_SNAPSHOT_ROOT"
    MLE_SOURCE_SNAPSHOT=$(mktemp -d "${MLE_SOURCE_SNAPSHOT_ROOT}/${RUN_TAG}_${COMP}.XXXXXX")
    archive_tracked_source "$MLE_AGENT_DIR" "$MLE_SOURCE_SNAPSHOT" "$MLE_SOURCE_COMMIT"
    MLE_RUN_ROOT_REL=${MLE_RUN_ROOT#"$MLE_AGENT_DIR"/}
    if [ "$MLE_RUN_ROOT_REL" != "$MLE_RUN_ROOT" ]; then
      mkdir -p "$MLE_SOURCE_SNAPSHOT/$MLE_RUN_ROOT_REL"
    fi

    MLEVOLVE_UV_VENV=${MLEVOLVE_UV_VENV:-${EAR}/BenchmarkAdapters/environments/agents/mlevolve/.venv}
    MLEVOLVE_PYTHON=${MLEVOLVE_PYTHON:-${MLEVOLVE_UV_VENV}/bin/python}
    MLEBENCH_PYTHON=${MLEBENCH_PYTHON:-${EAR}/mle-bench-lite/.venv/bin/python}
    AGENT_PYTHON=$MLEVOLVE_PYTHON
    test -x "$MLEVOLVE_PYTHON" || { echo "MLEvolve UV runtime is not installed: $MLEVOLVE_PYTHON" >&2; exit 2; }
    (
      cd "$MLE_SOURCE_SNAPSHOT"
      GRADING_SERVER_PORT=$GRADING_PORT DATASET_DIR=$MLE_BENCH_DATA_ROOT \
        "$MLEVOLVE_PYTHON" -u -m engine.validation.format_server \
        dataset_dir="$MLE_BENCH_DATA_ROOT" data_dir=none desc_file=none \
        >"$MLE_RUN_ROOT/format_server.log" 2>&1
    ) &
    HOST_FORMAT_SERVER_PID=$!
    FORMAT_READY=0
    for _ in $(seq 1 60); do
      if curl -fsS "http://127.0.0.1:${GRADING_PORT}/health" >/dev/null 2>&1; then
        FORMAT_READY=1
        break
      fi
      sleep 0.5
    done
    if [ "$FORMAT_READY" -ne 1 ]; then
      echo "MLEvolve format server failed; see $MLE_RUN_ROOT/format_server.log" >&2
      exit 2
    fi

    BENCHMARK_MOUNTS=(
      -v "${MLE_SOURCE_SNAPSHOT}:${MLE_AGENT_DIR}:ro"
      -v "${MLE_RUN_ROOT}:${MLE_RUN_ROOT}:rw"
      -v "${DATA}:${DATA}:ro"
      -v "${EAR}/BenchmarkAdapters/tcp_forwarder.py:${EAR}/BenchmarkAdapters/tcp_forwarder.py:ro"
    )
    add_uv_runtime_mounts "$MLEVOLVE_UV_VENV"
    MLE_ISOLATION_CHECKS="
      test -r '$DATA/description.md' || { echo 'MLEvolve isolation failed: public data is unavailable' >&2; exit 96; }
      test ! -e '${MLE_BENCH_DATA_ROOT}/${COMP}/prepared/private' || { echo 'MLEvolve isolation failed: private data is visible' >&2; exit 97; }
      test ! -e '${EAR}/mle-bench-research' || { echo 'MLEvolve isolation failed: research reports are visible' >&2; exit 98; }
      test ! -e '${EAR}/mle-bench-agents/efficient-auto-research' || { echo 'MLEvolve isolation failed: EAR is visible' >&2; exit 99; }
    "
    INNER_CMD="$MLE_ISOLATION_CHECKS
      set -e
      AGENT_DIR=$MLE_AGENT_DIR
      cd \$AGENT_DIR
      export PATH='$MLEVOLVE_UV_VENV/bin':\$PATH
      '$MLEVOLVE_PYTHON' '${EAR}/BenchmarkAdapters/tcp_forwarder.py' \
        --listen-port '$GRADING_PORT' --target-host '$RELAY_CONTAINER_HOST' --target-port '$GRADING_PORT' &
      FORMAT_FORWARDER_PID=\$!
      trap 'kill \$FORMAT_FORWARDER_PID 2>/dev/null || true' EXIT
      FUSION_TS=\$(date +%Y%m%d_%H%M%S)
      MLE_SEARCH_TIME=$((TIMEOUT > 180 ? TIMEOUT - 120 : TIMEOUT))
      set +e
      GRADING_SERVER_PORT=$GRADING_PORT DATASET_DIR=${MLE_BENCH_DATA_ROOT} \
      timeout --foreground --signal=TERM --kill-after=10s $TIMEOUT '$MLEVOLVE_PYTHON' run.py \
        exp_id=$COMP dataset_dir=${MLE_BENCH_DATA_ROOT} \
        data_dir=$DATA desc_file=$DATA/description.md \
        exp_name=$COMP \
        log_dir=$MLE_SESSION_ROOT workspace_dir=$MLE_SESSION_ROOT \
        'agent.code.model=$MODEL' 'agent.code.base_url=$PROXY_URL' 'agent.code.api_key=$RELAY_API_KEY' \
        'agent.feedback.model=$MODEL' 'agent.feedback.base_url=$PROXY_URL' 'agent.feedback.api_key=$RELAY_API_KEY' \
        agent.steps=$STEPS agent.time_limit=\$MLE_SEARCH_TIME agent.seed=$SEED \
        coldstart.use_coldstart=False \
        ${MLE_EXEC_TIMEOUT:+exec.timeout=$MLE_EXEC_TIMEOUT} \
        start_cpu_id=0 cpu_number=$CPUS_PER_TASK
      SEARCH_STATUS=\$?
      set -e
      if [ \$SEARCH_STATUS -ne 0 ] && [ \$SEARCH_STATUS -ne 124 ]; then
        echo \"MLEvolve search exited with status \$SEARCH_STATUS\" >&2
        exit \$SEARCH_STATUS
      fi
      echo '--- submission fusion (官方后处理) ---'
      '$MLEVOLVE_PYTHON' utils/submission_fusion_utils.py --task_id $COMP --exp_name \${FUSION_TS}_$COMP --runs_root $MLE_SESSION_ROOT
      FUSION_DIR=\$(find '$MLE_SESSION_ROOT' -type d -path '*/ensembles_csv' -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)
      test -n \"\$FUSION_DIR\" || { echo 'MLEvolve fusion produced no output directory' >&2; exit 102; }
      FUSION_FILE=\$(find \"\$FUSION_DIR\" -maxdepth 1 -type f -name '*.csv' -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)
      test -n \"\$FUSION_FILE\" || { echo 'MLEvolve fusion produced no CSV' >&2; exit 103; }
      cp \"\$FUSION_FILE\" '$MLE_RUN_ROOT/submission.csv'
    "
    echo "=== MLEvolve source snapshot: $MLE_SOURCE_SNAPSHOT ($MLE_SOURCE_COMMIT) ==="
    ;;
  *)
    echo "未知 agent: $AGENT"; exit 1 ;;
esac

echo "=== 启动容器 $CONTAINER_NAME (GPU index=$GPU_ID, uuid=$GPU_UUID, minor=$GPU_MINOR, steps=$STEPS, timeout=${TIMEOUT}s, cpuset=${CPUSET}, grading_port=$GRADING_PORT) ==="
echo "=== LLM 代理: 127.0.0.1:$PROXY_PORT → $UPSTREAM_BASE_URL  model: $MODEL  retries: $LLM_MAX_RETRIES ==="
echo "=== LLM token log: $TOKEN_LOG_PATH ==="

DOCKER_RUN_FLAGS=(--rm --name "$CONTAINER_NAME")
if [ "${DETACH:-0}" = "1" ]; then
  echo "DETACH=1 is unsupported because host relay/format services must share the run lifecycle" >&2
  exit 2
fi

# 注意 agent 侧环境变量：
#   OPENAI_BASE_URL / OPENAI_API_BASE → 本地代理 (openai sdk / litellm / aider 都认)
#   NO_PROXY 含 127.0.0.1 → agent 到代理的流量不走 Clash；代理到 relay 直连 (trust_env=False)
#   HTTP(S)_PROXY 保留 → HF 下模型仍走 Clash
docker run "${DOCKER_RUN_FLAGS[@]}" "${EXTRA_DOCKER_FLAGS[@]}" \
  --add-host "$RELAY_CONTAINER_HOST:host-gateway" \
  --device "$GPU_DEVICE":/dev/nvidia0 \
  --device /dev/nvidiactl:/dev/nvidiactl \
  --device /dev/nvidia-uvm:/dev/nvidia-uvm \
  --device /dev/nvidia-uvm-tools:/dev/nvidia-uvm-tools \
  --cpuset-cpus="$CPUSET" \
  --shm-size=8g \
  "${BENCHMARK_MOUNTS[@]}" \
  -v /usr/lib/x86_64-linux-gnu/libcuda.so.1:/usr/lib/x86_64-linux-gnu/libcuda.so.1:ro \
  -v /usr/lib/x86_64-linux-gnu/libnvidia-ml.so.1:/usr/lib/x86_64-linux-gnu/libnvidia-ml.so.1:ro \
  -v /usr/bin/nvidia-smi:/usr/bin/nvidia-smi:ro \
  -v ${HF_CACHE_HOST}:/root/.cache/huggingface \
  -e HF_HUB_OFFLINE=0 \
  -e HF_HOME=/root/.cache/huggingface \
  -e HF_HUB_CACHE=/root/.cache/huggingface/hub \
  -e TRANSFORMERS_CACHE=/root/.cache/huggingface/hub \
  -e HF_HUB_DISABLE_XET=1 \
  -e PYTHONUNBUFFERED=1 \
  -e CUDA_VISIBLE_DEVICES=0 \
  -e HTTP_PROXY=${CONTAINER_HTTP_PROXY} \
  -e HTTPS_PROXY=${CONTAINER_HTTP_PROXY} \
  -e NO_PROXY="localhost,127.0.0.1,$RELAY_CONTAINER_HOST" \
  -e no_proxy="localhost,127.0.0.1,$RELAY_CONTAINER_HOST" \
  -e OPENAI_API_KEY="$RELAY_API_KEY" \
  -e OPENAI_BASE_URL="$PROXY_URL" \
  -e OPENAI_API_BASE="$PROXY_URL" \
  "$CONTAINER_IMAGE" \
  bash -c "
    set -e
    echo '--- 容器内环境检查 ---'
    '$AGENT_PYTHON' --version
    '$AGENT_PYTHON' -c 'import torch; print(\"GPU:\", torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"NO GPU\")'
    echo '--- 使用宿主 LLM relay (port $PROXY_PORT) ---'
    echo '--- 跑 agent: $AGENT on $COMP ---'
    $INNER_CMD
  "
