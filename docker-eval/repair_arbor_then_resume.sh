#!/usr/bin/env bash
# Insert the missing Arbor three-task run before the live campaign continues.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
CAMPAIGN_DIR=$(realpath "${1:?provide the original campaign directory}")
CONTROLLER_PID=${2:?provide its live controller PID}
REPAIR_DIR=$(realpath -m "${3:?provide a fresh repair campaign directory}")
ADAPTER_PY="$ROOT/BenchmarkAdapters/.venv/bin/python"
PROTOCOL="$ROOT/BenchmarkAdapters/configs/mle-protocol.n1-12h.json"
STATE_FILE="$CAMPAIGN_DIR/priority-arbor-repair.json"

[[ "$CONTROLLER_PID" =~ ^[1-9][0-9]*$ ]]
[[ "$CAMPAIGN_DIR" == "$ROOT/experiment-campaigns/"* ]]
[[ "$REPAIR_DIR" == "$ROOT/experiment-campaigns/"* ]]
[[ -d "$CAMPAIGN_DIR" && ! -e "$REPAIR_DIR" ]]
[[ -z "$(git status --porcelain)" ]]
[[ -r "/proc/$CONTROLLER_PID/stat" ]]
tr '\0' '\n' < "/proc/$CONTROLLER_PID/cmdline" | \
  rg -q -x -F "$ROOT/docker-eval/launch_mle_7agent_3task.sh"
[[ "$(readlink "/proc/$CONTROLLER_PID/cwd")" == "$ROOT" ]]
[[ "$(tr -d '[:space:]' < "$CAMPAIGN_DIR/controller.pid")" == "$CONTROLLER_PID" ]]
CONTROLLER_START=$(awk '{print $22}' "/proc/$CONTROLLER_PID/stat")
if [[ "$(awk '{print $3}' "/proc/$CONTROLLER_PID/stat")" == T ]]; then
  "$ADAPTER_PY" - "$STATE_FILE" "$CONTROLLER_PID" "$CONTROLLER_START" <<'PY'
import json, sys
from pathlib import Path
path, pid, start = sys.argv[1:]
previous = json.loads(Path(path).read_text())
assert previous["original_controller_pid"] == int(pid)
assert previous["original_controller_start_ticks"] == start
assert previous["status"] in {
    "arbor_failed_original_controller_stays_paused",
    "invalid_arbor_results_original_controller_stays_paused",
    "interrupted_original_controller_stays_paused",
}, "the paused controller is not awaiting a failed repair retry"
PY
fi

exec 9>"$CAMPAIGN_DIR/priority-arbor-repair.lock"
flock -n 9
set -a
source "${MLE_RELAY_ENV_FILE:-$HOME/.mle_relay_env}"
set +a
"$ADAPTER_PY" -c 'from BenchmarkAdapters.MLEBenchLite.network import check_proxy, ensure_image; check_proxy(); ensure_image("alexgshaw/fix-git:20251031")'

SKIP_TASKS=$("$ADAPTER_PY" - "$PROTOCOL" <<'PY'
import json, sys
tasks = json.load(open(sys.argv[1]))["task_ids"]
wanted = {"aerial-cactus-identification", "aptos2019-blindness-detection", "denoising-dirty-documents"}
assert wanted <= set(tasks)
print(" ".join(task for task in tasks if task not in wanted))
PY
)

write_state() {
  "$ADAPTER_PY" - "$STATE_FILE" "$1" "$CONTROLLER_PID" "$CONTROLLER_START" "$REPAIR_DIR" <<'PY'
import json, os, sys
from datetime import datetime
from pathlib import Path
path, status, pid, start, repair = sys.argv[1:]
payload = {
    "status": status,
    "updated_at": datetime.now().astimezone().isoformat(),
    "original_controller_pid": int(pid),
    "original_controller_start_ticks": start,
    "repair_campaign": repair,
    "replaces_failed_round": 3,
    "resume_next_round": 7,
    "resume_next_agent": "ai-scientist",
    "policy": "resume original controller only after all three Arbor results are valid",
}
target = Path(path)
if target.exists():
    previous = json.loads(target.read_text())
    history = previous.get("previous_repair_campaigns", [])
    old_repair = previous.get("repair_campaign")
    if old_repair and old_repair != repair and old_repair not in history:
        history.append(old_repair)
    payload["previous_repair_campaigns"] = history
temporary = target.with_suffix(".tmp")
temporary.write_text(json.dumps(payload, indent=2) + "\n")
os.replace(temporary, target)
PY
}

trap 'write_state interrupted_original_controller_stays_paused; exit 130' INT TERM HUP
write_state pausing_original_controller
kill -STOP "$CONTROLLER_PID"
write_state waiting_or_running_arbor
printf 'Paused controller %s; priority Arbor campaign: %s\n' "$CONTROLLER_PID" "$REPAIR_DIR"

if env CAMPAIGN_DIR="$REPAIR_DIR" CAMPAIGN_ID="$(basename "$REPAIR_DIR")" \
  AGENT_LIST=arbor START_ROUND=1 SKIP_TASKS="$SKIP_TASKS" NOTIFY_ENABLED=0 \
  PROTOCOL="$PROTOCOL" \
  MODEL_CONFIG="$ROOT/BenchmarkAdapters/configs/model-track.gpt-5.6-terra-host-relay.json" \
  bash "$ROOT/docker-eval/launch_mle_7agent_3task.sh"; then
  if ! "$ADAPTER_PY" - "$REPAIR_DIR" <<'PY'
import json, sys
from pathlib import Path
files = list(Path(sys.argv[1]).glob("round-01/*/arbor/seed-0/*/result.json"))
results = [json.loads(path.read_text()) for path in files]
wanted = {"aerial-cactus-identification", "aptos2019-blindness-detection", "denoising-dirty-documents"}
assert len(results) == 3 and {result["task_id"] for result in results} == wanted
assert all(result["status"] == "completed" and result["score_valid"] is True for result in results)
PY
  then
    write_state invalid_arbor_results_original_controller_stays_paused
    exit 1
  fi
else
  write_state arbor_failed_original_controller_stays_paused
  exit 1
fi

if [[ ! -r "/proc/$CONTROLLER_PID/stat" ]] || \
  [[ "$(awk '{print $22}' "/proc/$CONTROLLER_PID/stat")" != "$CONTROLLER_START" ]]; then
  write_state arbor_completed_original_controller_missing
  exit 1
fi
kill -CONT "$CONTROLLER_PID"
write_state arbor_completed_original_controller_resumed
printf 'Arbor completed; resumed controller %s.\n' "$CONTROLLER_PID"
