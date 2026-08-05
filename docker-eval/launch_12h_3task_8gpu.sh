#!/bin/bash
# 12 小时三题双 Agent 对比跑，6 张卡并发。
#
# 题目矩阵：
#   Medium: chaii-hindi-and-tamil-question-answering
#   Low   : jigsaw-toxic-comment-classification-challenge
#   Low   : mlsp-2013-birds
#
# 容器矩阵：
#   2 agent × chaii  = 2
#   2 agent × jigsaw = 2
#   2 agent × mlsp   = 2
#
# 用法: bash launch_12h_3task_8gpu.sh

set -e

DIR=/mnt/sdc/shijianwang/efficient-agent-research/docker-eval
RUN_ID=$(date +%Y%m%d_%H%M%S)_12h_3task_8gpu
LOGDIR=$DIR/logs/$RUN_ID
mkdir -p "$LOGDIR"
export RUN_TAG=$RUN_ID

STEPS=9999
TIMEOUT=43200

CHAII=chaii-hindi-and-tamil-question-answering
JIGSAW=jigsaw-toxic-comment-classification-challenge
BIRDS=mlsp-2013-birds

echo "=== Run ID: $RUN_ID ==="
echo
echo "=== 启动 6 容器 (steps=$STEPS, timeout=${TIMEOUT}s) ==="

launch() {  # agent comp gpu
  local agent=$1 comp=$2 gpu=$3
  local log="$LOGDIR/${agent}_${comp}_gpu${gpu}.log"
  echo ">> $agent / $comp -> GPU $gpu  (log: $log)"
  nohup bash "$DIR/run_in_docker.sh" "$agent" "$comp" "$gpu" "$STEPS" "$TIMEOUT" > "$log" 2>&1 &
}

# GPU0-1: medium
launch efficient-auto-research "$CHAII"  0
launch MLEvolve                "$CHAII"  1

# GPU2-3: low from previous run
launch efficient-auto-research "$JIGSAW" 2
launch MLEvolve                "$JIGSAW" 3

# GPU4-5: low birds
launch efficient-auto-research "$BIRDS"  4
launch MLEvolve                "$BIRDS"  5

sleep 10
echo
echo "=== 已下发，当前容器状态 ==="
docker ps --filter "name=mle-" --format "  {{.Names}}: {{.Status}}"

cat <<EOF

=== 日志目录 ===
  $LOGDIR

=== 监控命令 ===
  watch -n 30 'docker ps --filter name=mle- --format "{{.Names}}: {{.Status}}"'
  watch -n 30 'nvidia-smi --query-gpu=index,memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits'
  tail -f $LOGDIR/efficient-auto-research_${CHAII}_gpu0.log

预计每个容器最多跑 12 小时。跑完后用 docker-eval/grade.py 汇总评分。
EOF
