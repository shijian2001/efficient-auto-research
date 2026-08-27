#!/usr/bin/env bash
# 7 Agent x 3 题 MLE-Bench Lite (Low) campaign，共 7 轮、21 格。
#
# 一轮 = 一个 Agent 跑 3 题（3 张卡并行）。轮与轮之间串行，任何时刻最多
# 3 格在跑，所以对 host relay 的并发压力恒定为 3 路。
#
# 题目是原 6 题研究子集里落在官方 Lite Low split 的那 3 道
# （spooky / jigsaw / mlsp）。另外 3 道在 Medium，mle-cell 会拒。
#
# 每轮开跑前等到有 3 张 GPU 连续空闲 GPU_STABILITY_SECONDS 秒。空闲判定同时看
# 显存和「有没有 compute app 占着」，并用 GPU UUID 关联，不假设 nvidia-smi 的
# 逻辑 index 等于 Linux device minor。
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

CAMPAIGN_ID=${CAMPAIGN_ID:-$(date +%Y%m%d_%H%M%S)_mle_7agent_3task}
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

# 通知：事件先落盘，再通过本机 MetaBot bridge 投递到当前群聊。
# 关闭通知只需设置 NOTIFY_ENABLED=0；事件文件仍会保留。
NOTIFY_CLI=${NOTIFY_CLI:-/home/shijianwang/metabot/bin/metabot}
NOTIFY_BOT=${NOTIFY_BOT:-default}
NOTIFY_CHAT_ID=${NOTIFY_CHAT_ID:-oc_f33d0a0994fa961b8d968c11f363cc0e}
NOTIFY_ENABLED=${NOTIFY_ENABLED:-1}
NOTIFY_TIMEOUT_SECONDS=${NOTIFY_TIMEOUT_SECONDS:-10}
EVENTS_FILE=${EVENTS_FILE:-$CAMPAIGN_DIR/events.jsonl}

FINAL_EVENT_SENT=0
CURRENT_ROUND=0
CURRENT_AGENT=""
CURRENT_VARIANT=""

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

declare -a TASKS=(
  "spooky-author-identification"
  "jigsaw-toxic-comment-classification-challenge"
  "mlsp-2013-birds"
)

log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$CAMPAIGN_DIR/orchestration.log"; }
log_stderr() { log "$@" >&2; }

# Append one JSON event with an exclusive file lock. Values are passed as
# key=value arguments so task names and error details remain correctly escaped.
emit_event() {
  local event_type=$1
  shift
  /usr/bin/env python3 - "$EVENTS_FILE" "$CAMPAIGN_ID" "$event_type" "$@" <<'PY' || true
import fcntl
import json
import os
import sys
import time
import uuid

path, campaign_id, event_type = sys.argv[1:4]
data = {}
for item in sys.argv[4:]:
    key, sep, value = item.partition("=")
    if sep:
        data[key] = value

payload = {
    "event_id": f"{campaign_id}:{event_type}:{uuid.uuid4().hex}",
    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "campaign_id": campaign_id,
    "type": event_type,
    "data": data,
}
os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
with open(path, "a", encoding="utf-8") as stream:
    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
    stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    stream.flush()
    os.fsync(stream.fileno())
    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
PY
}

notify_group() {
  local message=$1
  [[ "$NOTIFY_ENABLED" != "0" ]] || return 0
  if [[ ! -x "$NOTIFY_CLI" ]]; then
    log_stderr "通知未发送：MetaBot CLI 不可执行: $NOTIFY_CLI"
    return 0
  fi
  timeout "$NOTIFY_TIMEOUT_SECONDS" "$NOTIFY_CLI" talk \
    "$NOTIFY_BOT" "$NOTIFY_CHAT_ID" "$message" >/dev/null 2>&1 || \
    log_stderr "通知发送失败（事件已写入 $EVENTS_FILE）"
}

die() {
  local code=$1
  shift
  local detail=$*
  log_stderr "ERROR $detail"
  emit_event campaign_preflight_failed status=failed exit_code="$code" detail="$detail"
  notify_group "[Arbor监控] campaign=$CAMPAIGN_ID 前置检查失败，退出码=$code；原因=$detail"
  FINAL_EVENT_SENT=1
  exit "$code"
}

on_signal() {
  local signal_name=$1
  local exit_code=$2
  emit_event campaign_interrupted status=interrupted signal="$signal_name" \
    round="$CURRENT_ROUND" agent="$CURRENT_AGENT" variant="$CURRENT_VARIANT"
  notify_group "[Arbor监控] campaign=$CAMPAIGN_ID 收到 $signal_name，中途退出；当前轮=$CURRENT_ROUND，Agent=$CURRENT_AGENT。请检查 $CAMPAIGN_DIR/orchestration.log 和 events.jsonl。"
  FINAL_EVENT_SENT=1
  exit "$exit_code"
}

on_exit() {
  local rc=$?
  if (( FINAL_EVENT_SENT == 0 )); then
    emit_event process_exit status=$([[ "$rc" -eq 0 ]] && echo completed || echo failed) \
      exit_code="$rc" round="$CURRENT_ROUND" agent="$CURRENT_AGENT" variant="$CURRENT_VARIANT"
    notify_group "[Arbor监控] campaign=$CAMPAIGN_ID 控制脚本退出，退出码=$rc；当前轮=$CURRENT_ROUND，Agent=$CURRENT_AGENT。请检查 $CAMPAIGN_DIR/orchestration.log 和 events.jsonl。"
  fi
}

trap on_exit EXIT
trap 'on_signal TERM 143' TERM
trap 'on_signal INT 130' INT
trap 'on_signal HUP 129' HUP

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
        log_stderr "GPU 候选 [$candidate]，开始 ${GPU_STABILITY_SECONDS}s 稳定性计时"
      else
        local still=1
        for g in $candidate; do grep -qw "$g" <<<"$clean" || still=0; done
        if [ "$still" -eq 0 ]; then
          stable_since=$now; candidate=$clean
          log_stderr "候选集合变化，重新计时 [$candidate]"
        elif [ $((now - stable_since)) -ge "$GPU_STABILITY_SECONDS" ]; then
          awk -v n="$GPUS_PER_ROUND" '{for(i=1;i<=n;i++) printf "%s ", $i}' <<<"$candidate"
          return 0
        fi
      fi
    elif [ -n "$stable_since" ]; then
      stable_since=""; candidate=""
      log_stderr "空闲 GPU 不足 ($count < $GPUS_PER_ROUND)，取消计时"
    fi
    sleep "$POLL_SECONDS"
  done
}

# 跑一轮：一个 Agent x 3 题，各占一张卡，等三格都结束。
run_round() {
  local round=$1 agent=$2 variant=$3; shift 3
  local -a tasks=("$@") gpus pids=() names=()
  CURRENT_ROUND=$round
  CURRENT_AGENT=$agent
  CURRENT_VARIANT=$variant
  read -r -a gpus <<<"$(wait_for_stable_gpus)"
  log "轮 $round/$TOTAL_ROUNDS 开始: agent=$agent variant=$variant gpus=[${gpus[*]}]"

  local i
  for i in "${!tasks[@]}"; do
    local task=${tasks[$i]} gpu=${gpus[$i]}
    local run_dir="$CAMPAIGN_DIR/round-$(printf '%02d' "$round")/$task"
    local task_log="$CAMPAIGN_DIR/round-$(printf '%02d' "$round")-$task.log"
    mkdir -p "$(dirname "$run_dir")"
    log "  -> $task on GPU $gpu"
    emit_event task_started status=running round="$round" agent="$agent" variant="$variant" \
      task="$task" gpu_id="$gpu" run_dir="$run_dir" log="$task_log"
    env OPENAI_API_KEY="${OPENAI_API_KEY:-}" UPSTREAM_API_KEY="${UPSTREAM_API_KEY:-${OPENAI_API_KEY:-}}" \
      "$ADAPTER_PY" -m BenchmarkAdapters mle-cell \
        --protocol "$PROTOCOL" \
        --agent "$agent" --agent-variant "$variant" \
        --competition-id "$task" --seed "$SEED" \
        --data-root "$DATA_ROOT" \
        --campaign-dir "$run_dir" \
        --gpu-id "$gpu" \
        --model-config "$MODEL_CONFIG" \
        > "$task_log" 2>&1 &
    pids+=($!); names+=("$task")
  done

  local rc=0 idx child_rc
  for idx in "${!pids[@]}"; do
    if wait "${pids[$idx]}"; then
      log "  <- ${names[$idx]} 完成"
      emit_event task_finished status=completed round="$round" agent="$agent" variant="$variant" \
        task="${names[$idx]}" gpu_id="${gpus[$idx]}" pid="${pids[$idx]}" exit_code=0 \
        log="$CAMPAIGN_DIR/round-$(printf '%02d' "$round")-${names[$idx]}.log"
      notify_group "[Arbor监控] campaign=$CAMPAIGN_ID 轮=$round/$TOTAL_ROUNDS Agent=$agent 题目=${names[$idx]} 已完成，GPU=${gpus[$idx]}，退出码=0。"
    else
      child_rc=$?
      rc=1
      log "  <- ${names[$idx]} 失败 (见 round-$(printf '%02d' "$round")-${names[$idx]}.log)"
      emit_event task_finished status=failed round="$round" agent="$agent" variant="$variant" \
        task="${names[$idx]}" gpu_id="${gpus[$idx]}" pid="${pids[$idx]}" exit_code="$child_rc" \
        log="$CAMPAIGN_DIR/round-$(printf '%02d' "$round")-${names[$idx]}.log"
      notify_group "[Arbor监控] **问题** campaign=$CAMPAIGN_ID 轮=$round/$TOTAL_ROUNDS Agent=$agent 题目=${names[$idx]} 失败，GPU=${gpus[$idx]}，退出码=$child_rc；日志=$CAMPAIGN_DIR/round-$(printf '%02d' "$round")-${names[$idx]}.log"
    fi
  done
  # 一格失败不终止 campaign：它的 result.json 会记下失败原因，
  # 后续轮次仍然要跑，否则一个坏格子会吃掉整张表。
  log "轮 $round 结束 (rc=$rc)"
  if (( rc == 0 )); then
    emit_event round_finished status=completed round="$round" agent="$agent" variant="$variant"
  else
    emit_event round_finished status=failed round="$round" agent="$agent" variant="$variant"
  fi
  notify_group "[Arbor监控] campaign=$CAMPAIGN_ID 轮=$round/$TOTAL_ROUNDS 结束，Agent=$agent，状态=$([[ "$rc" -eq 0 ]] && echo completed || echo failed)。"
  return "$rc"
}

main() {
  mkdir -p "$CAMPAIGN_DIR"
  TOTAL_ROUNDS=${#AGENTS[@]}
  emit_event campaign_started status=starting total_rounds="$TOTAL_ROUNDS" tasks="${TASKS[*]}"

  # 先做硬前提检查，别等跑到第 8 轮才发现 relay 没起。
  [ -f "$PROTOCOL" ] || die 2 "缺少协议: $PROTOCOL"
  [ -f "$MODEL_CONFIG" ] || die 2 "缺少 model-track: $MODEL_CONFIG"
  [ -x "$ADAPTER_PY" ] || die 2 "缺少 adapter runtime: $ADAPTER_PY"
  if ! curl -s -m 20 -o /dev/null -w '%{http_code}' "$RELAY_BASE_URL/models" \
        -H "Authorization: Bearer ${OPENAI_API_KEY:-}" | grep -qE '^(200|404|405)$'; then
    die 2 "host relay 没有应答 $RELAY_BASE_URL —— 先起 LLMRelay.server，见 docs/CAMPAIGN_LAUNCH.md"
  fi
  if [ -n "$(git -C "$ROOT" status --porcelain)" ]; then
    die 2 "工作区不干净，formal_source_clean 会拒绝每一格；先提交再跑"
  fi

  log "campaign=$CAMPAIGN_ID 共 $TOTAL_ROUNDS 轮，每轮 1 个 Agent x 3 题，${GPUS_PER_ROUND} 张卡"
  log "协议=$PROTOCOL"
  log "model-track=$MODEL_CONFIG"

  local round=0 agent variant failed_rounds=0
  for agent in "${AGENTS[@]}"; do
    round=$((round + 1))
    variant=${VARIANT_OVERRIDE[$agent]:-$(resolve_variant "$agent")}
    if run_round "$round" "$agent" "$variant" "${TASKS[@]}"; then
      :
    else
      failed_rounds=$((failed_rounds + 1))
    fi
  done

  if (( failed_rounds == 0 )); then
    emit_event campaign_finished status=completed total_rounds="$TOTAL_ROUNDS" failed_rounds=0
    notify_group "[Arbor监控] campaign=$CAMPAIGN_ID 全部 $TOTAL_ROUNDS 轮完成，21 个任务格已结束；结果目录=$CAMPAIGN_DIR。"
    FINAL_EVENT_SENT=1
    log "全部 $TOTAL_ROUNDS 轮结束。结果在 $CAMPAIGN_DIR"
  else
    emit_event campaign_finished status=failed total_rounds="$TOTAL_ROUNDS" failed_rounds="$failed_rounds"
    notify_group "[Arbor监控] **问题** campaign=$CAMPAIGN_ID 全部 $TOTAL_ROUNDS 轮结束，但有 $failed_rounds 轮包含失败任务；请检查 $CAMPAIGN_DIR/orchestration.log、events.jsonl 和各轮日志。"
    FINAL_EVENT_SENT=1
    log "全部 $TOTAL_ROUNDS 轮结束，但有 $failed_rounds 轮失败。结果在 $CAMPAIGN_DIR"
    return 1
  fi
}

main "$@"
