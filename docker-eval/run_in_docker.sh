#!/bin/bash
# 在 Docker 容器中跑单个 agent (sudo-free GPU + 宿主代理)
# 支持: efficient-auto-research / Arbor / MLEvolve
#
# 关键设计：
#   - 复用宿主 conda 环境（只读挂载 miniconda3），不构建重镜像
#   - GPU: --device 手动挂 nvidia 设备 + 宿主 libcuda（无需 nvidia-container-toolkit / sudo）
#   - 固定单卡: 只挂 /dev/nvidia${GPU_ID} → 容器内 nvidia0 + CUDA_VISIBLE_DEVICES=0
#   - 网络: --network host 复用宿主 Clash 代理 (127.0.0.1:17892) 下 HF 模型
#   - HF 在线: 挂宿主 HF cache(可写) + HF_HUB_OFFLINE=0 + NO_PROXY 不含 HF,经 Clash 下模型(XLM-R 等多语言模型 chaii 必需)
#   - 路径一致挂载（容器内路径 == 宿主路径），避免 pip editable 包路径错位
#
#   - LLM 走本地转发代理 (llm_relay_proxy.py)，agent 代码保持纯上游零侵入：
#       agent → http://127.0.0.1:$PROXY_PORT/v1 → relay (gpt-5.5)
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

cleanup_host_services() {
  if [ -n "$HOST_FORMAT_SERVER_PID" ]; then
    kill "$HOST_FORMAT_SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup_host_services EXIT INT TERM

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
LLM_UPSTREAM_PROXY=${LLM_UPSTREAM_PROXY:-$CLASH_PROXY}
ROOT=/mnt/sdc/shijianwang
EAR=${ROOT}/efficient-agent-research
ALIGNED_KNOWLEDGE_ROOT=${ALIGNED_KNOWLEDGE_ROOT:-${EAR}/ear-worktrees/g3-mlevolve-knowledge-aligned/configs/mlevolve_coldstart}

# --- LLM 上游 (代理转发目标) ---
UPSTREAM_BASE_URL=${UPSTREAM_BASE_URL:-${LLM_BASE_URL:-https://relay.shuai-ederson-clow.xyz/v1}}
UPSTREAM_API_KEY=${UPSTREAM_API_KEY:-${OPENAI_API_KEY:-}}
if [ -z "$UPSTREAM_API_KEY" ]; then
  echo "缺少上游 API credential：请设置 UPSTREAM_API_KEY 或 OPENAI_API_KEY" >&2
  exit 2
fi
export UPSTREAM_API_KEY
# The client SDK requires OPENAI_API_KEY even though requests target the local
# relay. Forward the host-provided credential by variable name, never as a
# launcher literal or command-line value.
export OPENAI_API_KEY="$UPSTREAM_API_KEY"
MODEL=${MODEL:-gpt-5.5}
LLM_REASONING_EFFORT=${LLM_REASONING_EFFORT:-${OPENAI_REASONING_EFFORT:-high}}
LLM_UPSTREAM_TIMEOUT=${LLM_UPSTREAM_TIMEOUT:-}   # 空 = 不限
LLM_MAX_RETRIES=${LLM_MAX_RETRIES:-20}

# --- 本地转发代理 ---
PROXY_PORT=$((6200 + GPU_ID))
PROXY_URL="http://127.0.0.1:${PROXY_PORT}/v1"

DATA=${EAR}/mle-bench-data/${COMP}/prepared/public
HF_CACHE_HOST=${HF_CACHE_HOST:-${EAR}/cache/huggingface}
MLE_CACHE_HOST=${MLE_CACHE_HOST:-${HOME}/.cache/mle-bench}
TOKEN_LOG_DIR=${EAR}/run-logs/${RUN_TAG}_token_usage
TOKEN_LOG_PATH=${TOKEN_LOG_DIR}/${AGENT}_${COMP}_gpu${GPU_ID}.jsonl
mkdir -p "$TOKEN_LOG_DIR"
mkdir -p "$HF_CACHE_HOST" "$MLE_CACHE_HOST"

# MLEvolve retains the historical broad mount layout. EAR overrides this
# below with an allowlist so it cannot inspect private labels, prior runs, or
# other agents' code and reports.
BENCHMARK_MOUNTS=(
  -v "${EAR}:${EAR}"
  -v "${MLE_CACHE_HOST}:/root/.cache/mle-bench"
  -v "${HOME}/.kaggle:/root/.kaggle:ro"
)

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

# 容器内先起转发代理，agent 的 LLM 流量全部走它
PROXY_CMD="
  UPSTREAM_BASE_URL=$UPSTREAM_BASE_URL UPSTREAM_API_KEY=\$UPSTREAM_API_KEY \
  LLM_UPSTREAM_PROXY=$LLM_UPSTREAM_PROXY \
  LLM_FORCE_MODEL=$MODEL LLM_REASONING_EFFORT=$LLM_REASONING_EFFORT \
  LLM_UPSTREAM_TIMEOUT=$LLM_UPSTREAM_TIMEOUT LLM_MAX_RETRIES=$LLM_MAX_RETRIES \
  LLM_TOKEN_LOG_PATH=$TOKEN_LOG_PATH LLM_PROXY_AGENT_NAME=$AGENT \
  nohup python -u ${EAR}/docker-eval/llm_relay_proxy.py --port $PROXY_PORT \
    > /tmp/llm_proxy_${GPU_ID}.log 2>&1 &
  for i in \$(seq 1 20); do
    python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:$PROXY_PORT/health', timeout=2)\" 2>/dev/null && break
    sleep 0.5
  done
  python -c \"import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:$PROXY_PORT/health', timeout=2).read().decode())\" \
    || { echo 'LLM 代理启动失败'; cat /tmp/llm_proxy_${GPU_ID}.log; exit 1; }
"

# 各 agent 在容器内的启动命令
case "$AGENT" in
  efficient-auto-research)
    # 版本迭代: EAR_AGENT_DIR 可指向某个 worktree (见 efficient-auto-research/docs/ITERATION.md)，
    # 默认跑主工作目录。容器只接收当前 commit 的干净快照，不挂载整个 EAR。
    EAR_AGENT_DIR=${EAR_AGENT_DIR:-${EAR}/mle-bench-agents/$AGENT}
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
      EAR_SOURCE_DIRTY=$(test -n "$(git -C "$EAR_AGENT_DIR" status --porcelain)" && echo true || echo false)
      EAR_OUTPUT_DIR=$EAR_AGENT_DIR/docker_runs/${RUN_TAG}_$COMP
      echo "=== EAR G3 legacy CLI: $EAR_AGENT_DIR ==="
      echo "=== EAR 宿主 provenance: $EAR_SOURCE_BRANCH @ $EAR_SOURCE_COMMIT (dirty=$EAR_SOURCE_DIRTY) ==="
      INNER_CMD="
        AGENT_DIR=$EAR_AGENT_DIR
        OUT=\$AGENT_DIR/docker_runs/${RUN_TAG}_$COMP; mkdir -p \$OUT
        cd \$AGENT_DIR
        python -u agent/run.py --data_dir $DATA --desc_file $DATA/description.md \\
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
    EAR_SOURCE_COMMIT=$(git -C "$EAR_AGENT_DIR" rev-parse HEAD)
    EAR_SOURCE_BRANCH=$(git -C "$EAR_AGENT_DIR" rev-parse --abbrev-ref HEAD)
    EAR_SOURCE_DIRTY=$(test -n "$(git -C "$EAR_AGENT_DIR" status --porcelain)" && echo true || echo false)
    EAR_OUTPUT_DIR=$EAR_AGENT_DIR/docker_runs/${RUN_TAG}_$COMP
    mkdir -p "$EAR_OUTPUT_DIR"
    LAUNCHER_SHA256=$(sha256sum "$SCRIPT_PATH" | awk '{print $1}')
    RELAY_SHA256=$(sha256sum "${EAR}/docker-eval/llm_relay_proxy.py" | awk '{print $1}')
    LAUNCH_MANIFEST=$EAR_OUTPUT_DIR/launch_manifest.json
    LAUNCH_MANIFEST_TMP=$(mktemp "${LAUNCH_MANIFEST}.tmp.XXXXXX")
    MANIFEST_COMP="$COMP" MANIFEST_RUN_ID="$RUN_TAG" MANIFEST_SEED="$SEED" \
    MANIFEST_MODEL="$MODEL" MANIFEST_REASONING_EFFORT="$LLM_REASONING_EFFORT" \
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
      ${ROOT}/miniconda3/envs/mlebench/bin/python - "$LAUNCH_MANIFEST_TMP" <<'PY'
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
    "temperature": 1.0,
    "reasoning_effort": os.environ["MANIFEST_REASONING_EFFORT"],
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
    INNER_CMD="
      AGENT_DIR=$EAR_AGENT_DIR
      OUT=\$AGENT_DIR/docker_runs/${RUN_TAG}_$COMP; mkdir -p \$OUT
      cd \$AGENT_DIR
      python -u agent/run.py --data_dir $DATA --desc_file $DATA/description.md \
        --output \$OUT/submission.csv --max_steps $STEPS --timeout $TIMEOUT --model $MODEL \
        --temperature 1.0 \
        --seed $SEED --run-id $RUN_TAG \
        --initial-root-attempts $EAR_INITIAL_ROOT_ATTEMPTS \
        --new-root-stagnation $EAR_NEW_ROOT_STAGNATION \
        --new-root-cooldown-attempts $EAR_NEW_ROOT_COOLDOWN_ATTEMPTS \
        --launch-manifest-sha256 $LAUNCH_MANIFEST_SHA256 \
        --source-commit $EAR_SOURCE_COMMIT --source-branch $EAR_SOURCE_BRANCH \
        --source-dirty $EAR_SOURCE_DIRTY --require-source-provenance
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
    git -C "$EAR_AGENT_DIR" archive "$EAR_SOURCE_COMMIT" | tar -x -C "$EAR_SOURCE_SNAPSHOT"

    EAR_OUTPUT_REL=${EAR_OUTPUT_DIR#"$EAR_AGENT_DIR"/}
    if [ "$EAR_OUTPUT_REL" = "$EAR_OUTPUT_DIR" ]; then
      echo "EAR 输出目录必须位于源码目录内: $EAR_OUTPUT_DIR" >&2
      exit 2
    fi
    # Docker needs the nested mountpoint to exist before the source snapshot is
    # mounted read-only.
    mkdir -p "$EAR_SOURCE_SNAPSHOT/$EAR_OUTPUT_REL"

    BENCHMARK_MOUNTS=(
      -v "${EAR_SOURCE_SNAPSHOT}:${EAR_AGENT_DIR}:ro"
      -v "${EAR_OUTPUT_DIR}:${EAR_OUTPUT_DIR}:rw"
      -v "${DATA}:${DATA}:ro"
      -v "${EAR}/docker-eval/llm_relay_proxy.py:${EAR}/docker-eval/llm_relay_proxy.py:ro"
      -v "${TOKEN_LOG_DIR}:${TOKEN_LOG_DIR}:rw"
    )

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
      test ! -e '${EAR}/mle-bench-data/${COMP}/prepared/private' || { echo 'EAR isolation failed: private data is visible' >&2; exit 97; }
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
    ARBOR_DIR=${ARBOR_DIR:-${EAR}/baselines/Arbor}
    ARBOR_OUTPUT_DIR=${ARBOR_OUTPUT_DIR:-${EAR}/run-logs/${RUN_TAG}_Arbor_${COMP}_gpu${GPU_ID}}
    mkdir -p "$ARBOR_OUTPUT_DIR"
    if [ ! -d "$ARBOR_DIR/.git" ]; then
      echo "Arbor repository not found: $ARBOR_DIR" >&2
      exit 2
    fi
    if [ -n "$(git -C "$ARBOR_DIR" status --porcelain)" ]; then
      echo "Arbor isolation requires a clean source tree: $ARBOR_DIR" >&2
      exit 2
    fi
    ARBOR_SOURCE_COMMIT=$(git -C "$ARBOR_DIR" rev-parse HEAD)
    ARBOR_SOURCE_SNAPSHOT_ROOT=${ARBOR_SOURCE_SNAPSHOT_ROOT:-${EAR}/cache/arbor-source-snapshots}
    mkdir -p "$ARBOR_SOURCE_SNAPSHOT_ROOT"
    ARBOR_SOURCE_SNAPSHOT=$(mktemp -d "${ARBOR_SOURCE_SNAPSHOT_ROOT}/${RUN_TAG}_${COMP}.XXXXXX")
    git -C "$ARBOR_DIR" archive "$ARBOR_SOURCE_COMMIT" | tar -x -C "$ARBOR_SOURCE_SNAPSHOT"
    for required_adapter_file in src/mle/run.py src/mle/eval_runner.py src/mle/adapter.py; do
      if [ ! -f "$ARBOR_SOURCE_SNAPSHOT/$required_adapter_file" ]; then
        echo "Arbor archived commit is missing adapter file: $required_adapter_file" >&2
        exit 2
      fi
    done

    ARBOR_PYTHONPATH_ROOT="$ARBOR_OUTPUT_DIR/pythonpath"
    mkdir -p "$ARBOR_PYTHONPATH_ROOT"
    ln -s "$ARBOR_SOURCE_SNAPSHOT/src" "$ARBOR_PYTHONPATH_ROOT/arbor"
    MLEBENCH_PYTHON=${MLEBENCH_PYTHON:-${ROOT}/miniconda3/envs/mlebench/bin/python}
    ARBOR_PYTHON=${ARBOR_PYTHON:-${ROOT}/miniconda3/envs/arbor/bin/python}
    ARBOR_METRIC_DIRECTION=$(PYTHONPATH="$ARBOR_PYTHONPATH_ROOT" "$MLEBENCH_PYTHON" -c \
      "from pathlib import Path; from arbor.mle.common import infer_metric_direction; print(infer_metric_direction('$COMP', Path('$DATA')))" )
    ARBOR_TIME_BUDGET=$((TIMEOUT > 300 ? TIMEOUT - 120 : TIMEOUT))

    PYTHONPATH="$ARBOR_PYTHONPATH_ROOT" "$MLEBENCH_PYTHON" -u -m arbor.mle.format_server \
      --data-root "${EAR}/mle-bench-data" --competition-id "$COMP" --port "$GRADING_PORT" \
      >"$ARBOR_OUTPUT_DIR/format_server.log" 2>&1 &
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
      echo "Arbor format server failed; see $ARBOR_OUTPUT_DIR/format_server.log" >&2
      exit 2
    fi

    BENCHMARK_MOUNTS=(
      -v "${ARBOR_SOURCE_SNAPSHOT}:${ARBOR_SOURCE_SNAPSHOT}:ro"
      -v "${ARBOR_OUTPUT_DIR}:${ARBOR_OUTPUT_DIR}:rw"
      -v "${DATA}:${DATA}:ro"
      -v "${EAR}/docker-eval/llm_relay_proxy.py:${EAR}/docker-eval/llm_relay_proxy.py:ro"
      -v "${TOKEN_LOG_DIR}:${TOKEN_LOG_DIR}:rw"
    )
    ARBOR_ISOLATION_CHECKS="
      test -r '$DATA/description.md' || { echo 'Arbor isolation failed: public data is unavailable' >&2; exit 96; }
      test ! -e '${EAR}/mle-bench-data/${COMP}/prepared/private' || { echo 'Arbor isolation failed: private data is visible' >&2; exit 97; }
      test ! -e '/private/data' || { echo 'Arbor isolation failed: /private/data is visible' >&2; exit 98; }
      test ! -e '${EAR}/baselines/MLEvolve' || { echo 'Arbor isolation failed: MLEvolve is visible' >&2; exit 99; }
      test ! -e '${EAR}/mle-bench-research' || { echo 'Arbor isolation failed: research reports are visible' >&2; exit 100; }
    "
    INNER_CMD="$ARBOR_ISOLATION_CHECKS
      export PYTHONPATH='$ARBOR_PYTHONPATH_ROOT'
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
    INNER_CMD="
      AGENT_DIR=$MLE_AGENT_DIR
      cd \$AGENT_DIR
      # 起 grading server (format 校验)
      GRADING_SERVER_PORT=$GRADING_PORT DATASET_DIR=${EAR}/mle-bench-data \
        nohup python -u -m engine.validation.format_server \
        dataset_dir=${EAR}/mle-bench-data data_dir=none desc_file=none > /tmp/grading_$GPU_ID.log 2>&1 &
      sleep 8
      FUSION_TS=\$(date +%Y%m%d_%H%M%S)
      GRADING_SERVER_PORT=$GRADING_PORT DATASET_DIR=${EAR}/mle-bench-data \
      timeout --foreground --signal=TERM --kill-after=10s $TIMEOUT python run.py \
        exp_id=$COMP dataset_dir=${EAR}/mle-bench-data \
        data_dir=$DATA desc_file=$DATA/description.md \
        exp_name=$COMP \
        log_dir=$MLE_RUN_ROOT workspace_dir=$MLE_RUN_ROOT \
        'agent.code.model=$MODEL' 'agent.code.base_url=$PROXY_URL' 'agent.code.api_key=proxy' \
        'agent.feedback.model=$MODEL' 'agent.feedback.base_url=$PROXY_URL' 'agent.feedback.api_key=proxy' \
        agent.steps=$STEPS agent.time_limit=$TIMEOUT \
        coldstart.use_coldstart=False \
        ${MLE_EXEC_TIMEOUT:+exec.timeout=$MLE_EXEC_TIMEOUT} \
        start_cpu_id=0 cpu_number=$CPUS_PER_TASK
      echo '--- submission fusion (官方后处理) ---'
      python utils/submission_fusion_utils.py --task_id $COMP --exp_name \${FUSION_TS}_$COMP --runs_root $MLE_RUN_ROOT || true
    "
    ;;
  *)
    echo "未知 agent: $AGENT"; exit 1 ;;
esac

echo "=== 启动容器 $CONTAINER_NAME (GPU index=$GPU_ID, uuid=$GPU_UUID, minor=$GPU_MINOR, steps=$STEPS, timeout=${TIMEOUT}s, cpuset=${CPUSET}, grading_port=$GRADING_PORT) ==="
echo "=== LLM 代理: 127.0.0.1:$PROXY_PORT → $UPSTREAM_BASE_URL  model: $MODEL  effort: $LLM_REASONING_EFFORT  retries: $LLM_MAX_RETRIES ==="
echo "=== LLM token log: $TOKEN_LOG_PATH ==="

DOCKER_RUN_FLAGS=(--rm --name "$CONTAINER_NAME")
if [ "${DETACH:-0}" = "1" ]; then
  DOCKER_RUN_FLAGS=(-d "${DOCKER_RUN_FLAGS[@]}")
  echo "=== Docker detach mode: docker logs -f $CONTAINER_NAME ==="
fi

# 注意 agent 侧环境变量：
#   OPENAI_BASE_URL / OPENAI_API_BASE → 本地代理 (openai sdk / litellm / aider 都认)
#   NO_PROXY 含 127.0.0.1 → agent 到代理的流量不走 Clash；代理到 relay 直连 (trust_env=False)
#   HTTP(S)_PROXY 保留 → HF 下模型仍走 Clash
docker run "${DOCKER_RUN_FLAGS[@]}" "${EXTRA_DOCKER_FLAGS[@]}" \
  --network host \
  --device "$GPU_DEVICE":/dev/nvidia0 \
  --device /dev/nvidiactl:/dev/nvidiactl \
  --device /dev/nvidia-uvm:/dev/nvidia-uvm \
  --device /dev/nvidia-uvm-tools:/dev/nvidia-uvm-tools \
  --cpuset-cpus="$CPUSET" \
  --shm-size=8g \
  -v ${ROOT}/miniconda3:${ROOT}/miniconda3:ro \
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
  -e HTTP_PROXY=${CLASH_PROXY} \
  -e HTTPS_PROXY=${CLASH_PROXY} \
  -e NO_PROXY="localhost,127.0.0.1" \
  -e no_proxy="localhost,127.0.0.1" \
  -e OPENAI_API_KEY \
  -e UPSTREAM_API_KEY \
  -e OPENAI_BASE_URL="$PROXY_URL" \
  -e OPENAI_API_BASE="$PROXY_URL" \
  "$CONTAINER_IMAGE" \
  bash -c "
    export PATH=${ROOT}/miniconda3/envs/mlebench/bin:${ROOT}/miniconda3/bin:\$PATH
    echo '--- 容器内环境检查 ---'
    python --version
    python -c 'import torch; print(\"GPU:\", torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"NO GPU\")'
    echo '--- 起 LLM 转发代理 (port $PROXY_PORT) ---'
    $PROXY_CMD
    echo '--- 跑 agent: $AGENT on $COMP ---'
    $INNER_CMD
  "
