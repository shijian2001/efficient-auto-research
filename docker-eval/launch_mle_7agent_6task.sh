#!/usr/bin/env bash
# 7 Agent x 6 题 MLE-Bench Lite campaign，共 14 轮。
#
# 一轮 = 一个 Agent 跑 3 题（3 张卡并行）。轮与轮之间串行，任何时刻最多
# 3 格在跑，所以对 host relay 的并发压力恒定为 3 路。
#
#   轮 1-7 : 7 个 Agent 依次跑「前 3 题」
#   轮 8-14: 7 个 Agent 依次跑「后 3 题」
#
# 题目分组沿用 2026-08-23 那次 12h campaign 的 group1/group2，便于和历史结果对照。
#
# 每轮开跑前等到有 3 张 GPU 连续空闲 GPU_STABILITY_SECONDS 秒。空闲判定同时看
# 显存和「有没有 compute app 占着」，并用 GPU UUID 关联，不假设 nvidia-smi 的
# 逻辑 index 等于 Linux device minor。
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

CAMPAIGN_ID=${CAMPAIGN_ID:-$(date +%Y%m%d_%H%M%S)_mle_7agent_6task}
CAMPAIGN_DIR=${CAMPAIGN_DIR:-$ROOT/experiment-campaigns/$CAMPAIGN_ID}
PROTOCOL=${PROTOCOL:-$ROOT/BenchmarkAdapters/configs/mle-protocol.n1-12h.json}
MODEL_CONFIG=${MODEL_CONFIG:-$ROOT/BenchmarkAdapters/configs/model-track.gpt-5.6-terra-host-relay.json}
DATA_ROOT=${DATA_ROOT:-$ROOT/mle-bench-data}
SEED=${SEED:-0}
ADAPTER_PY=${ADAPTER_PY:-$ROOT/BenchmarkAdapters/.venv/bin/python}

# 空闲判据
GPU_STABILITY_SECONDS=${GPU_STABILITY_SECONDS:-180}
GPU_MEMORY_LIMIT_MIB=${GPU_MEMORY_LIMIT_MIB:-1024}
GPU_UTIL_LIMIT_PERCENT=${GPU_UTIL_LIMIT_PERCENT:-5}
GPUS_PER_ROUND=${GPUS_PER_ROUND:-3}
POLL_SECONDS=${POLL_SECONDS:-30}
EXCLUDE_GPU_IDS=${EXCLUDE_GPU_IDS:-}

RELAY_BASE_URL=${RELAY_BASE_URL:-http://127.0.0.1:6200/v1}

# 原版 ID 的三家必须钉到本机 pin。那串哈希从 thin_registry.UPSTREAM_REVISIONS
# 现读，不写死：手抄过一次就抄错了，而且每次 re-pin 内层仓库都会让写死的值过期。
resolve_variant() {
  "$ADAPTER_PY" -c "
import sys
sys.path.insert(0, '$ROOT')
from BenchmarkAdapters.thin_registry import UPSTREAM_REVISIONS
a = '$1'
print(a + '@' + UPSTREAM_REVISIONS[a] if a in UPSTREAM_REVISIONS else a)
"
}

declare -a AGENTS=(
  "ear" "mlevolve" "arbor" "codex" "claude-code" "ml-master-2" "ai-scientist"
)

# Arbor 的 MLE 格只能经 patched variant 进入；原版 ID 会被直接拒绝。
declare -A VARIANT_OVERRIDE=( ["arbor"]="arbor-benchmark-patched" )

declare -a GROUP1=(
  "spooky-author-identification"
  "tweet-sentiment-extraction"
  "learning-agency-lab-automated-essay-scoring-2"
)
declare -a GROUP2=(
  "jigsaw-toxic-comment-classification-challenge"
  "mlsp-2013-birds"
  "chaii-hindi-and-tamil-question-answering"
)

log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$CAMPAIGN_DIR/orchestration.log"; }

# 打印当前「干净」的 GPU index。干净 = 显存低于阈值、利用率低于阈值，
# 且没有任何 compute app 占用（按 UUID 关联）。
clean_gpu_indices() {
  /usr/bin/env python3 - "$GPU_MEMORY_LIMIT_MIB" "$GPU_UTIL_LIMIT_PERCENT" "$EXCLUDE_GPU_IDS" <<'PY'
import csv, subprocess, sys

mem_limit = int(sys.argv[1]); util_limit = int(sys.argv[2])
excluded = {x.strip() for x in sys.argv[3].split(",") if x.strip()}

def query(args):
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL)

try:
    gpu_rows = list(csv.reader(query([
        "nvidia-smi", "--query-gpu=index,uuid,memory.used,utilization.gpu",
        "--format=csv,noheader,nounits"]).splitlines()))
except Exception:
    sys.exit(0)

busy_uuids = set()
try:
    for row in csv.reader(query([
        "nvidia-smi", "--query-compute-apps=gpu_uuid,pid,process_name,used_memory",
        "--format=csv,noheader,nounits"]).splitlines()):
        if row and row[0].strip():
            busy_uuids.add(row[0].strip())
except subprocess.CalledProcessError:
    pass

clean = []
for row in gpu_rows:
    if len(row) < 4:
        continue
    index, uuid, mem, util = (c.strip() for c in row[:4])
    if index in excluded or uuid in busy_uuids:
        continue
    try:
        if int(mem) <= mem_limit and int(util) <= util_limit:
            clean.append(index)
    except ValueError:
        continue
print(" ".join(clean))
PY
}

# 阻塞直到有 GPUS_PER_ROUND 张卡「连续」空闲 GPU_STABILITY_SECONDS 秒。
# 稳定窗口内集合一旦缩小就重新计时——避免刚被释放、马上又被别人抢走的卡。
wait_for_stable_gpus() {
  local stable_since="" candidate=""
  while true; do
    local now clean count
    now=$(date +%s)
    clean=$(clean_gpu_indices)
    count=$(wc -w <<<"$clean")
    if [ "$count" -ge "$GPUS_PER_ROUND" ]; then
      if [ -z "$stable_since" ]; then
        stable_since=$now; candidate=$clean
        log "GPU 候选 [$candidate]，开始 ${GPU_STABILITY_SECONDS}s 稳定性计时"
      else
        local still=1
        for g in $candidate; do grep -qw "$g" <<<"$clean" || still=0; done
        if [ "$still" -eq 0 ]; then
          stable_since=$now; candidate=$clean
          log "候选集合变化，重新计时 [$candidate]"
        elif [ $((now - stable_since)) -ge "$GPU_STABILITY_SECONDS" ]; then
          awk -v n="$GPUS_PER_ROUND" '{for(i=1;i<=n;i++) printf "%s ", $i}' <<<"$candidate"
          return 0
        fi
      fi
    elif [ -n "$stable_since" ]; then
      stable_since=""; candidate=""
      log "空闲 GPU 不足 ($count < $GPUS_PER_ROUND)，取消计时"
    fi
    sleep "$POLL_SECONDS"
  done
}

# 跑一轮：一个 Agent x 3 题，各占一张卡，等三格都结束。
run_round() {
  local round=$1 agent=$2 variant=$3; shift 3
  local -a tasks=("$@") gpus pids=() names=()
  read -r -a gpus <<<"$(wait_for_stable_gpus)"
  log "轮 $round/$TOTAL_ROUNDS 开始: agent=$agent variant=$variant gpus=[${gpus[*]}]"

  local i
  for i in "${!tasks[@]}"; do
    local task=${tasks[$i]} gpu=${gpus[$i]}
    local run_dir="$CAMPAIGN_DIR/round-$(printf '%02d' "$round")/$task"
    mkdir -p "$(dirname "$run_dir")"
    log "  -> $task on GPU $gpu"
    env OPENAI_API_KEY="${OPENAI_API_KEY:-}" UPSTREAM_API_KEY="${UPSTREAM_API_KEY:-${OPENAI_API_KEY:-}}" \
      "$ADAPTER_PY" -m BenchmarkAdapters mle-cell \
        --protocol "$PROTOCOL" \
        --agent "$agent" --agent-variant "$variant" \
        --competition-id "$task" --seed "$SEED" \
        --data-root "$DATA_ROOT" \
        --campaign-dir "$run_dir" \
        --gpu-id "$gpu" \
        --model-config "$MODEL_CONFIG" \
        > "$CAMPAIGN_DIR/round-$(printf '%02d' "$round")-$task.log" 2>&1 &
    pids+=($!); names+=("$task")
  done

  local rc=0 idx
  for idx in "${!pids[@]}"; do
    if wait "${pids[$idx]}"; then
      log "  <- ${names[$idx]} 完成"
    else
      rc=1
      log "  <- ${names[$idx]} 失败 (见 round-$(printf '%02d' "$round")-${names[$idx]}.log)"
    fi
  done
  # 一格失败不终止 campaign：它的 result.json 会记下失败原因，
  # 后续轮次仍然要跑，否则一个坏格子会吃掉整张表。
  log "轮 $round 结束 (rc=$rc)"
}

main() {
  mkdir -p "$CAMPAIGN_DIR"
  TOTAL_ROUNDS=$(( ${#AGENTS[@]} * 2 ))

  # 先做硬前提检查，别等跑到第 8 轮才发现 relay 没起。
  [ -f "$PROTOCOL" ] || { echo "缺少协议: $PROTOCOL" >&2; exit 2; }
  [ -f "$MODEL_CONFIG" ] || { echo "缺少 model-track: $MODEL_CONFIG" >&2; exit 2; }
  [ -x "$ADAPTER_PY" ] || { echo "缺少 adapter runtime: $ADAPTER_PY" >&2; exit 2; }
  if ! curl -s -m 20 -o /dev/null -w '%{http_code}' "$RELAY_BASE_URL/models" \
        -H "Authorization: Bearer ${OPENAI_API_KEY:-}" | grep -qE '^(200|404|405)$'; then
    echo "host relay 没有应答 $RELAY_BASE_URL —— 先起 LLMRelay.server，见 docs/CAMPAIGN_LAUNCH.md" >&2
    exit 2
  fi
  if [ -n "$(git -C "$ROOT" status --porcelain)" ]; then
    echo "工作区不干净，formal_source_clean 会拒绝每一格；先提交再跑" >&2
    exit 2
  fi

  log "campaign=$CAMPAIGN_ID 共 $TOTAL_ROUNDS 轮，每轮 1 个 Agent x 3 题，${GPUS_PER_ROUND} 张卡"
  log "协议=$PROTOCOL"
  log "model-track=$MODEL_CONFIG"

  local round=0 agent variant
  for agent in "${AGENTS[@]}"; do
    round=$((round + 1))
    variant=${VARIANT_OVERRIDE[$agent]:-$(resolve_variant "$agent")}
    run_round "$round" "$agent" "$variant" "${GROUP1[@]}"
  done
  for agent in "${AGENTS[@]}"; do
    round=$((round + 1))
    variant=${VARIANT_OVERRIDE[$agent]:-$(resolve_variant "$agent")}
    run_round "$round" "$agent" "$variant" "${GROUP2[@]}"
  done

  log "全部 $TOTAL_ROUNDS 轮结束。结果在 $CAMPAIGN_DIR"
}

main "$@"
