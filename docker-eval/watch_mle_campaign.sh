#!/usr/bin/env bash

set -Eeuo pipefail

CAMPAIGN_DIR=${1:?usage: watch_mle_campaign.sh <campaign-dir> <controller-pid>}
CONTROLLER_PID=${2:?usage: watch_mle_campaign.sh <campaign-dir> <controller-pid>}
EVENTS_FILE=${EVENTS_FILE:-$CAMPAIGN_DIR/events.jsonl}
NOTIFY_CLI=${NOTIFY_CLI:-/home/shijianwang/metabot/bin/metabot}
NOTIFY_BOT=${NOTIFY_BOT:-default}
NOTIFY_CHAT_ID=${NOTIFY_CHAT_ID:-oc_f33d0a0994fa961b8d968c11f363cc0e}
NOTIFY_ENABLED=${NOTIFY_ENABLED:-1}
NOTIFY_TIMEOUT_SECONDS=${NOTIFY_TIMEOUT_SECONDS:-10}
POLL_SECONDS=${POLL_SECONDS:-30}

terminal_event_seen() {
  [[ -f "$EVENTS_FILE" ]] || return 1
  rg -q '"type":"(campaign_finished|campaign_preflight_failed|campaign_interrupted|process_exit)"' "$EVENTS_FILE"
}

notify_group() {
  local message=$1
  [[ "$NOTIFY_ENABLED" != "0" ]] || return 0
  [[ -x "$NOTIFY_CLI" ]] || return 0
  timeout "$NOTIFY_TIMEOUT_SECONDS" "$NOTIFY_CLI" talk \
    "$NOTIFY_BOT" "$NOTIFY_CHAT_ID" "$message" >/dev/null 2>&1 || true
}

while kill -0 "$CONTROLLER_PID" 2>/dev/null; do
  sleep "$POLL_SECONDS"
done

# Give the controller's EXIT trap a moment to flush its terminal event.
sleep 2
if ! terminal_event_seen; then
  printf '{"event_id":"watchdog:controller_missing:%s","timestamp":"%s","type":"controller_missing","data":{"controller_pid":"%s","campaign_dir":"%s"}}\n' \
    "$(date +%s%N)" "$(date -Is)" "$CONTROLLER_PID" "$CAMPAIGN_DIR" >> "$EVENTS_FILE"
  notify_group "[Arbor监控] **大问题** campaign=$(basename "$CAMPAIGN_DIR") 控制 PID=$CONTROLLER_PID 已消失，且没有正常终结事件；可能是 SIGKILL/OOM/宿主故障。请检查 $CAMPAIGN_DIR/orchestration.log、events.jsonl 和各轮日志。"
fi
