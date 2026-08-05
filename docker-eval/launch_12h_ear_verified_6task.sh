#!/usr/bin/env bash
# Run the frozen verified-outcomes EAR iteration on all six research tasks.

set -euo pipefail

ROOT=/mnt/sdc/shijianwang/efficient-agent-research
DIR=$ROOT/docker-eval
EAR_AGENT_DIR=$ROOT/ear-worktrees/verified-outcomes
EXPECTED_COMMIT=7be8152b096ef028fa2e205e870557d47a57eca0
RUN_ID=${RUN_ID:-$(date +%Y%m%d_%H%M%S)_12h_ear_g4_verified_6task}
LOGDIR=$DIR/logs/$RUN_ID
MANIFEST=$LOGDIR/launch_manifest.txt

STEPS=9999
TIMEOUT=43200

COMMIT=$(git -C "$EAR_AGENT_DIR" rev-parse HEAD)
BRANCH=$(git -C "$EAR_AGENT_DIR" branch --show-current)
DIRTY=$(test -n "$(git -C "$EAR_AGENT_DIR" status --porcelain)" && echo true || echo false)

if [[ "$COMMIT" != "$EXPECTED_COMMIT" ]]; then
  echo "Refusing to run unexpected EAR commit: $COMMIT" >&2
  exit 1
fi
if [[ "$DIRTY" != false ]]; then
  echo "Refusing to run a dirty EAR worktree: $EAR_AGENT_DIR" >&2
  exit 1
fi

declare -a TASKS=(
  "spooky-author-identification:1"
  "tweet-sentiment-extraction:2"
  "learning-agency-lab-automated-essay-scoring-2:4"
  "jigsaw-toxic-comment-classification-challenge:5"
  "mlsp-2013-birds:6"
  "chaii-hindi-and-tamil-question-answering:7"
)

for spec in "${TASKS[@]}"; do
  comp=${spec%:*}
  data=$ROOT/mle-bench-data/$comp/prepared/public
  if [[ ! -f "$data/description.md" ]]; then
    echo "Prepared task is missing description.md: $data" >&2
    exit 1
  fi
done

mkdir -p "$LOGDIR"

cat > "$MANIFEST" <<EOF
run_id=$RUN_ID
started_at=$(date --iso-8601=seconds)
agent=efficient-auto-research
agent_dir=$EAR_AGENT_DIR
git_branch=$BRANCH
git_commit=$COMMIT
git_dirty=$DIRTY
model=${MODEL:-gpt-5.5}
reasoning_effort=${LLM_REASONING_EFFORT:-high}
steps=$STEPS
timeout_seconds=$TIMEOUT
result_policy=single_frozen_version_no_historical_stitching
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
    bash "$DIR/run_in_docker.sh" efficient-auto-research "$comp" "$gpu" "$STEPS" "$TIMEOUT" \
    > "$log" 2>&1 &
  echo "$! $gpu $comp $log" >> "$LOGDIR/launcher_pids.txt"
}

echo "=== Run ID: $RUN_ID ==="
echo "=== EAR: $BRANCH @ $COMMIT (dirty=$DIRTY) ==="

for spec in "${TASKS[@]}"; do
  launch "${spec%:*}" "${spec##*:}"
done

sleep 12

echo "=== Containers ==="
docker ps --filter "name=mle-efficient-auto-research-" \
  --format '{{.Names}}: {{.Status}}'
echo "=== Logs: $LOGDIR ==="
