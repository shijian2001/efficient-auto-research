#!/usr/bin/env bash
# Wait for six physically clean GPUs and launch one G7 six-task run.
# This watcher never stops processes it did not start and exits after one
# successful launch.

set -Eeuo pipefail

ROOT=/mnt/sdc/shijianwang/efficient-agent-research
DIR=$ROOT/docker-eval
AGENT_DIR=${EAR_AGENT_DIR:-$ROOT/ear-worktrees/g7-converged}
LAUNCHER=$DIR/launch_12h_ear_latest_6task.sh
CREDENTIALS_FILE=${EAR_CREDENTIALS_FILE:-/home/shijianwang/.config/ear/credentials.env}
POLL_INTERVAL=${POLL_INTERVAL:-30}
STATE_DIR=${STATE_DIR:-$DIR/logs/g7_gpu_wait}
WATCH_LOG=$STATE_DIR/watcher.log
STATE_FILE=$STATE_DIR/status.env
LOCK_FILE=$STATE_DIR/watcher.lock

mkdir -p "$STATE_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  printf 'another watcher already holds %s\n' "$LOCK_FILE" >&2
  exit 0
fi

now() { date '+%Y-%m-%d %H:%M:%S%z'; }

log() {
  printf '[%s] %s\n' "$(now)" "$*" | tee -a "$WATCH_LOG"
}

write_state() {
  local state=$1
  local clean=${2:-}
  local run_id=${3:-}
  local tmp="$STATE_FILE.tmp.$$"
  {
    printf 'state=%s\n' "$state"
    printf 'checked_at=%s\n' "$(now)"
    printf 'clean_gpu_indices=%s\n' "$clean"
    printf 'run_id=%s\n' "$run_id"
  } > "$tmp"
  mv -f "$tmp" "$STATE_FILE"
}

# Join GPU UUIDs with compute-app rows. Logical nvidia-smi index and Linux
# device minor are not assumed to be the same.
clean_gpu_indices() {
  /usr/bin/python3 - <<'PY'
import csv
import subprocess

def query(args):
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL)

gpu_rows = list(csv.reader(query([
    'nvidia-smi', '--query-gpu=index,uuid,memory.used,utilization.gpu',
    '--format=csv,noheader,nounits'
]).splitlines()))
busy = set()
try:
    app_rows = csv.reader(query([
        'nvidia-smi',
        '--query-compute-apps=gpu_uuid,pid,process_name,used_memory',
        '--format=csv,noheader,nounits'
    ]).splitlines())
    for row in app_rows:
        if row and row[0].strip():
            busy.add(row[0].strip())
except subprocess.CalledProcessError:
    pass

for row in gpu_rows:
    if len(row) < 4:
        continue
    index, uuid, used, util = [x.strip() for x in row[:4]]
    try:
        clean = uuid not in busy and int(float(used)) == 0 and int(float(util)) == 0
    except ValueError:
        clean = False
    if clean:
        print(index)
PY
}

target_containers_present() {
  local names
  names=$(docker ps --format '{{.Names}}' 2>/dev/null || true)
  printf '%s\n' "$names" | grep -E -x \
    'mle-efficient-auto-research-(spooky-author-identification|tweet-sentiment-extraction|learning-agency-lab-automated-essay-scoring-2|jigsaw-toxic-comment-classification-challenge|mlsp-2013-birds|chaii-hindi-and-tamil-question-answering)-gpu[0-9]+' \
    >/dev/null 2>&1
}

if [ ! -r "$CREDENTIALS_FILE" ]; then
  write_state 'blocked_credentials'
  log "credentials file is missing or unreadable: $CREDENTIALS_FILE"
  exit 2
fi
if [ ! -x "$LAUNCHER" ]; then
  write_state 'blocked_launcher'
  log "launcher is missing or not executable: $LAUNCHER"
  exit 2
fi

log "watching for six clean physical GPUs (poll=${POLL_INTERVAL}s)"
write_state 'waiting'

while :; do
  clean=()
  while IFS= read -r gpu; do
    [ -n "$gpu" ] && clean+=("$gpu")
  done < <(clean_gpu_indices || true)
  clean_csv=$(IFS=,; printf '%s' "${clean[*]:-}")
  write_state 'waiting' "$clean_csv"

  if [ "${#clean[@]}" -lt 6 ]; then
    log "clean GPUs=${#clean[@]}/6 indices=${clean_csv:-none}; waiting"
    sleep "$POLL_INTERVAL"
    continue
  fi

  if target_containers_present; then
    write_state 'blocked_existing_containers' "$clean_csv"
    log 'target EAR containers already exist; refusing duplicate launch'
    exit 3
  fi

  # Re-read immediately before launch to reduce the race with another job.
  clean_again=()
  while IFS= read -r gpu; do
    [ -n "$gpu" ] && clean_again+=("$gpu")
  done < <(clean_gpu_indices || true)
  if [ "${#clean_again[@]}" -lt 6 ]; then
    log 'clean GPU set changed during final check; retrying'
    sleep 2
    continue
  fi
  clean=("${clean_again[@]}")

  # Keep task order stable while allowing any six clean logical indices.
  run_id=${RUN_ID:-$(date +%Y%m%d_%H%M%S)_12h_ear_g7_6task}
  launch_log=$DIR/logs/$run_id/watcher_launcher.log
  mkdir -p "$(dirname "$launch_log")"

  # shellcheck disable=SC1090
  source "$CREDENTIALS_FILE"
  : "${UPSTREAM_API_KEY:?credentials file did not set UPSTREAM_API_KEY}"
  export OPENAI_API_KEY="$UPSTREAM_API_KEY"
  export EAR_AGENT_DIR="$AGENT_DIR"
  export RUN_ID="$run_id"
  export GPU_SPOOKY="${clean[0]}"
  export GPU_TWEET="${clean[1]}"
  export GPU_ESSAY="${clean[2]}"
  export GPU_JIGSAW="${clean[3]}"
  export GPU_MLSP="${clean[4]}"
  export GPU_CHAII="${clean[5]}"

  log "six clean GPUs found: ${clean[*]}; launching run_id=$run_id"
  write_state 'launching' "$clean_csv" "$run_id"
  if ! bash "$LAUNCHER" > "$launch_log" 2>&1; then
    write_state 'launch_failed' "$clean_csv" "$run_id"
    log "launcher failed; see $launch_log"
    exit 4
  fi

  sleep 5
  if target_containers_present; then
    write_state 'launched' "$clean_csv" "$run_id"
    log "run launched successfully: $run_id; launcher log=$launch_log"
    exit 0
  fi

  write_state 'launch_unverified' "$clean_csv" "$run_id"
  log "launcher returned but target containers were not observed; see $launch_log"
  exit 5
done
