#!/usr/bin/env bash
# Re-run the frozen G3 agent on the same six tasks for a 12-hour stability check.

set -euo pipefail

ROOT=/mnt/sdc/shijianwang/efficient-agent-research
DIR=$ROOT/docker-eval
EAR_AGENT_DIR=$ROOT/ear-worktrees/stagnation-cache
EXPECTED_COMMIT=7cd9ed5c1db0ff5250faad373e5d5a67209e604c
RUN_ID=${RUN_ID:-$(date +%Y%m%d_%H%M%S)_12h_ear_g3_twice_6task}
LOGDIR=$DIR/logs/$RUN_ID
MANIFEST=$LOGDIR/launch_manifest.txt

STEPS=9999
TIMEOUT=43200
MODEL=${MODEL:-gpt-5.5}
LLM_REASONING_EFFORT=${LLM_REASONING_EFFORT:-high}

declare -a TASKS=(
  "spooky-author-identification:1"
  "tweet-sentiment-extraction:2"
  "learning-agency-lab-automated-essay-scoring-2:4"
  "jigsaw-toxic-comment-classification-challenge:5"
  "mlsp-2013-birds:6"
  "chaii-hindi-and-tamil-question-answering:7"
)

COMMIT=$(git -C "$EAR_AGENT_DIR" rev-parse HEAD)
BRANCH=$(git -C "$EAR_AGENT_DIR" branch --show-current)
DIRTY=$(test -n "$(git -C "$EAR_AGENT_DIR" status --porcelain)" && echo true || echo false)

if [[ "$COMMIT" != "$EXPECTED_COMMIT" ]]; then
  echo "Refusing to run unexpected G3 commit: $COMMIT" >&2
  exit 1
fi
if [[ "$DIRTY" != false ]]; then
  echo "Refusing to run a dirty G3 worktree: $EAR_AGENT_DIR" >&2
  exit 1
fi
if [[ -z "${UPSTREAM_API_KEY:-${OPENAI_API_KEY:-}}" ]]; then
  echo "Missing UPSTREAM_API_KEY or OPENAI_API_KEY" >&2
  exit 1
fi
if [[ -e "$LOGDIR" ]]; then
  echo "Refusing to overwrite existing log directory: $LOGDIR" >&2
  exit 1
fi

# Fail before launching anything unless all six selected GPUs and all output
# paths are clean. This keeps the rerun comparable to the first G3 run.
for spec in "${TASKS[@]}"; do
  comp=${spec%:*}
  gpu=${spec##*:}
  data=$ROOT/mle-bench-data/$comp/prepared/public
  output=$EAR_AGENT_DIR/docker_runs/${RUN_ID}_$comp
  container=mle-efficient-auto-research-${comp}-gpu${gpu}

  if [[ ! -f "$data/description.md" ]]; then
    echo "Prepared task is missing description.md: $data" >&2
    exit 1
  fi
  if [[ -e "$output" ]]; then
    echo "Refusing to reuse existing run output: $output" >&2
    exit 1
  fi
  if [[ -n "$(docker ps -aq --filter "name=^/${container}$")" ]]; then
    echo "Refusing to replace existing container: $container" >&2
    exit 1
  fi

  gpu_processes=$(nvidia-smi --query-compute-apps=pid --format=csv,noheader -i "$gpu" | sed '/^[[:space:]]*$/d')
  gpu_memory=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i "$gpu" | tr -d '[:space:]')
  if [[ -n "$gpu_processes" || ! "$gpu_memory" =~ ^[0-9]+$ || "$gpu_memory" -gt 32 ]]; then
    echo "GPU $gpu is not clean (memory=${gpu_memory:-unknown} MiB, processes=${gpu_processes:-none})" >&2
    exit 1
  fi
done

mkdir -p "$LOGDIR"

STARTED_AT=$(date --iso-8601=seconds)
EXPECTED_END=$(date --iso-8601=seconds --date="+12 hours")
LAUNCHER_SHA256=$(sha256sum "$0" | awk '{print $1}')
RUNNER_SHA256=$(sha256sum "$DIR/run_in_docker.sh" | awk '{print $1}')
RELAY_SHA256=$(sha256sum "$DIR/llm_relay_proxy.py" | awk '{print $1}')

cat > "$MANIFEST" <<EOF
run_id=$RUN_ID
label=g3_twice
started_at=$STARTED_AT
expected_end=$EXPECTED_END
agent=efficient-auto-research
agent_cli_mode=g3_legacy
agent_dir=$EAR_AGENT_DIR
git_branch=$BRANCH
git_commit=$COMMIT
git_dirty=$DIRTY
model=$MODEL
temperature=1.0
reasoning_effort=$LLM_REASONING_EFFORT
seed=not_configurable_in_frozen_g3
steps=$STEPS
timeout_seconds=$TIMEOUT
result_policy=single_frozen_run_no_historical_stitching
launcher_sha256=$LAUNCHER_SHA256
runner_sha256=$RUNNER_SHA256
relay_sha256=$RELAY_SHA256
gpu1=spooky-author-identification
gpu2=tweet-sentiment-extraction
gpu4=learning-agency-lab-automated-essay-scoring-2
gpu5=jigsaw-toxic-comment-classification-challenge
gpu6=mlsp-2013-birds
gpu7=chaii-hindi-and-tamil-question-answering
EOF

launch() {
  local comp=$1
  local gpu=$2
  local log=$LOGDIR/efficient-auto-research_${comp}_gpu${gpu}.log

  echo ">> $comp -> GPU $gpu ($log)"
  nohup env RUN_TAG="$RUN_ID" EAR_AGENT_DIR="$EAR_AGENT_DIR" \
    EAR_CLI_MODE=g3_legacy MODEL="$MODEL" \
    LLM_REASONING_EFFORT="$LLM_REASONING_EFFORT" \
    bash "$DIR/run_in_docker.sh" efficient-auto-research "$comp" "$gpu" "$STEPS" "$TIMEOUT" \
    > "$log" 2>&1 &
  echo "$! $gpu $comp $log" >> "$LOGDIR/launcher_pids.txt"
}

echo "=== Run ID: $RUN_ID ==="
echo "=== G3: $BRANCH @ $COMMIT (dirty=$DIRTY) ==="
echo "=== Expected end: $EXPECTED_END ==="

for spec in "${TASKS[@]}"; do
  launch "${spec%:*}" "${spec##*:}"
done

sleep 15

running=0
echo "=== Containers ==="
for spec in "${TASKS[@]}"; do
  comp=${spec%:*}
  gpu=${spec##*:}
  container=mle-efficient-auto-research-${comp}-gpu${gpu}
  if [[ "$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null || true)" == true ]]; then
    running=$((running + 1))
    echo "$container: running"
  else
    echo "$container: NOT RUNNING" >&2
  fi
done

if [[ "$running" -ne 6 ]]; then
  echo "Only $running/6 containers are running; inspect $LOGDIR" >&2
  exit 1
fi

echo "=== Logs: $LOGDIR ==="
