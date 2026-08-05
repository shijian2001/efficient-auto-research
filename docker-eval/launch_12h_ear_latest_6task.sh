#!/usr/bin/env bash
# Run the latest EAR worktree on the six research tasks for 12 hours each.

set -euo pipefail

ROOT=/mnt/sdc/shijianwang/efficient-agent-research
DIR=$ROOT/docker-eval
EAR_AGENT_DIR=${EAR_AGENT_DIR:-$ROOT/ear-worktrees/g7-converged}
RUN_ID=${RUN_ID:-$(date +%Y%m%d_%H%M%S)_12h_ear_g7_6task}
LOGDIR=$DIR/logs/$RUN_ID
MANIFEST=$LOGDIR/launch_manifest.txt

STEPS=9999
TIMEOUT=43200
EAR_INITIAL_ROOT_ATTEMPTS=${EAR_INITIAL_ROOT_ATTEMPTS:-3}
EAR_NEW_ROOT_STAGNATION=${EAR_NEW_ROOT_STAGNATION:-8}
EAR_NEW_ROOT_COOLDOWN_ATTEMPTS=${EAR_NEW_ROOT_COOLDOWN_ATTEMPTS:-4}
# GPU assignment is explicit and overridable so a busy card can be avoided
# without changing the agent code or the experiment protocol.
GPU_SPOOKY=${GPU_SPOOKY:-1}
GPU_TWEET=${GPU_TWEET:-2}
GPU_ESSAY=${GPU_ESSAY:-4}
GPU_JIGSAW=${GPU_JIGSAW:-5}
GPU_MLSP=${GPU_MLSP:-6}
GPU_CHAII=${GPU_CHAII:-7}

mkdir -p "$LOGDIR"

COMMIT=$(git -C "$EAR_AGENT_DIR" rev-parse HEAD)
BRANCH=$(git -C "$EAR_AGENT_DIR" branch --show-current)
DIRTY=$(test -n "$(git -C "$EAR_AGENT_DIR" status --porcelain)" && echo true || echo false)

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
initial_root_attempts=$EAR_INITIAL_ROOT_ATTEMPTS
new_root_stagnation=$EAR_NEW_ROOT_STAGNATION
new_root_cooldown_attempts=$EAR_NEW_ROOT_COOLDOWN_ATTEMPTS
gpu${GPU_SPOOKY}=spooky-author-identification
gpu${GPU_TWEET}=tweet-sentiment-extraction
gpu${GPU_ESSAY}=learning-agency-lab-automated-essay-scoring-2
gpu${GPU_JIGSAW}=jigsaw-toxic-comment-classification-challenge
gpu${GPU_MLSP}=mlsp-2013-birds
gpu${GPU_CHAII}=chaii-hindi-and-tamil-question-answering
EOF

launch() {
  local comp=$1
  local gpu=$2
  local log=$LOGDIR/efficient-auto-research_${comp}_gpu${gpu}.log

  echo ">> $comp -> GPU $gpu ($log)"
  nohup env RUN_TAG="$RUN_ID" EAR_AGENT_DIR="$EAR_AGENT_DIR" \
    EAR_INITIAL_ROOT_ATTEMPTS="$EAR_INITIAL_ROOT_ATTEMPTS" \
    EAR_NEW_ROOT_STAGNATION="$EAR_NEW_ROOT_STAGNATION" \
    EAR_NEW_ROOT_COOLDOWN_ATTEMPTS="$EAR_NEW_ROOT_COOLDOWN_ATTEMPTS" \
    bash "$DIR/run_in_docker.sh" efficient-auto-research "$comp" "$gpu" "$STEPS" "$TIMEOUT" \
    > "$log" 2>&1 &
  echo "$! $gpu $comp $log" >> "$LOGDIR/launcher_pids.txt"
}

echo "=== Run ID: $RUN_ID ==="
echo "=== EAR: $BRANCH @ $COMMIT (dirty=$DIRTY) ==="

launch spooky-author-identification "$GPU_SPOOKY"
launch tweet-sentiment-extraction "$GPU_TWEET"
launch learning-agency-lab-automated-essay-scoring-2 "$GPU_ESSAY"
launch jigsaw-toxic-comment-classification-challenge "$GPU_JIGSAW"
launch mlsp-2013-birds "$GPU_MLSP"
launch chaii-hindi-and-tamil-question-answering "$GPU_CHAII"

sleep 12

echo "=== Containers ==="
docker ps --filter "name=mle-efficient-auto-research-" \
  --format '{{.Names}}: {{.Status}}'
echo "=== Logs: $LOGDIR ==="
