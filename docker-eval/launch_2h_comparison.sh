#!/bin/bash
# 4 容器 2 小时双 Agent 对比跑。
# 2 个 agent × 2 道题(chaii / jigsaw)= 4 个容器，各占一张卡(GPU 0-3)，GPU 4-7 空出。
# 不限步数(STEPS=9999)，固定 2 小时墙钟(TIMEOUT=7200)，墙钟是唯一约束。
#
# 用法: bash launch_2h_comparison.sh
#
# trace 保留(全部在挂载树下，--rm 后仍在)：
#   efficient: docker_runs/<comp>/workspace/{traces/step_*.json, step_*.py, report.json, submission.csv}
#   MLEvolve : runs/<ts>_docker_<comp>/logs/{journal.json, MLEvolve.verbose.log, MLEvolve.log, config.yaml, best_solution.py}
#   另外每个容器的全量 stdout 都 tee 到 docker-eval/logs/<agent>_<comp>_gpu<id>.log

set -e

DIR=/mnt/sdc/shijianwang/efficient-agent-research/docker-eval
LOGDIR=$DIR/logs
mkdir -p "$LOGDIR"

STEPS=9999
TIMEOUT=7200
CHAII=chaii-hindi-and-tamil-question-answering
JIGSAW=jigsaw-toxic-comment-classification-challenge

# 启动 4 个容器
# 布局: GPU0 ear/chaii  GPU1 mle/chaii
#       GPU2 ear/jigsaw GPU3 mle/jigsaw
echo
echo "=== 启动 4 容器 (steps=$STEPS, timeout=${TIMEOUT}s) ==="

launch() {  # agent comp gpu
  local agent=$1 comp=$2 gpu=$3
  local log="$LOGDIR/${agent}_${comp}_gpu${gpu}.log"
  echo ">> $agent / $comp -> GPU $gpu  (log: $log)"
  nohup bash "$DIR/run_in_docker.sh" "$agent" "$comp" "$gpu" "$STEPS" "$TIMEOUT" > "$log" 2>&1 &
}

launch efficient-auto-research "$CHAII"  0
launch MLEvolve                "$CHAII"  1
launch efficient-auto-research "$JIGSAW" 2
launch MLEvolve                "$JIGSAW" 3

sleep 5
echo
echo "=== 已下发，当前容器状态 ==="
docker ps --filter "name=mle-" --format "  {{.Names}}: {{.Status}}"

cat <<EOF

=== 监控命令 ===
  watch -n 30 'docker ps --filter name=mle- --format "{{.Names}}: {{.Status}}"'
  tail -f $LOGDIR/efficient-auto-research_${CHAII}_gpu0.log
  # 实时最优指标/步数(efficient & MLEvolve):
  cat .../docker_runs/<comp>/workspace/report.json | python -m json.tool

预计 ~2 小时后跑完(efficient 收尾重跑最优可能再多至多 ~40 分钟)。
跑完后用 grade.py 评分汇总。
EOF
