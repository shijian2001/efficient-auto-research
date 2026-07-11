#!/bin/bash
# 为一次 EAR 迭代开一个隔离的 git worktree + 分支，方便对比和回退。
#
# 用法:
#   bash scripts/new_iteration.sh <name> [base_ref]
#     <name>     迭代名 (kebab-case)，如 no-self-obs / stagnation-detect
#     [base_ref] 从哪个 ref 分叉，默认当前 HEAD
#
# 产出:
#   worktree 目录: <EAR>/ear-worktrees/<name>   (容器已挂载整个 EAR，可直接跑评测)
#   新分支:        iter/<name>
#
# 跑完后如何用它评测 / 回退 → docs/ITERATION.md
set -e

NAME=${1:?用法: new_iteration.sh <name> [base_ref]}
BASE_REF=${2:-HEAD}

# 仓库根 (脚本在 <repo>/scripts/ 下)
REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# EAR 根 = repo 的上两级 (mle-bench-agents/efficient-auto-research → efficient-agent-research)
EAR_ROOT=$(cd "$REPO_ROOT/../.." && pwd)
WT_BASE="$EAR_ROOT/ear-worktrees"
WT_DIR="$WT_BASE/$NAME"
BRANCH="iter/$NAME"

if [ -e "$WT_DIR" ]; then
  echo "✗ worktree 已存在: $WT_DIR" >&2
  echo "  切进去继续: cd $WT_DIR   |   删除重建: git -C $REPO_ROOT worktree remove $WT_DIR" >&2
  exit 1
fi

mkdir -p "$WT_BASE"
cd "$REPO_ROOT"

BASE_SHA=$(git rev-parse --short "$BASE_REF")
echo "=== 从 $BASE_REF ($BASE_SHA) 建 worktree ==="
git worktree add -b "$BRANCH" "$WT_DIR" "$BASE_REF"

cat <<EOF

✓ 迭代 worktree 就绪
    目录:   $WT_DIR
    分支:   $BRANCH  (基于 $BASE_SHA)

下一步:
  1. 改代码:   cd $WT_DIR && \$EDITOR agent/engine/thompson.py
  2. 提交:     git add -A && git commit -m "..."
  3. 跑评测:   cd $EAR_ROOT/docker-eval
               EAR_AGENT_DIR=$WT_DIR RUN_TAG=<tag> \\
                 bash run_in_docker.sh efficient-auto-research <comp> <gpu> <steps> <timeout>
  4. 看效果:   python $REPO_ROOT/scripts/compare_runs.py
  5. 回退:     丢弃 → git -C $REPO_ROOT worktree remove --force $WT_DIR && git -C $REPO_ROOT branch -D $BRANCH

详见 docs/ITERATION.md
EOF
