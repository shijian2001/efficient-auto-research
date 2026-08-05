# EAR 迭代工作流（版本可回退）

## 为什么要这套流程

以前 EAR 的每一版改进都**线性堆在 `proxy-based-eval` 一条分支**上，评测结果目录只用时间戳区分，而**跑它的代码 commit 没被记录**。结果就是：

- 某次迭代退步了（如 `107e437` 的 self-observation 让 chaii 从 0.588 掉到 0.541），想回退只能手动 `git revert`，还容易把同批次里有价值的改动（ensemble）一起丢掉；
- 想知道"这个 0.541 的结果到底是哪版代码跑的"，只能靠 commit 时间和目录时间戳去猜；
- Docker 评测直接跑工作区当前文件、不 checkout，所以**同一时刻只能跑一个版本，跑到一半不能改代码**——无法 baseline 和新版同时对比。

这套流程用 **git worktree（一版一目录一分支）+ report.json 里的 git 戳** 解决上面三点：每版代码隔离、结果可反查版本、baseline 和新版能并发对比、回退就是删一个 worktree。

## 一次迭代的完整链路

### 0. 前提
- 主工作目录：`mle-bench-agents/efficient-auto-research`（当前在 `proxy-based-eval` 分支）
- 评测框架：`../../docker-eval`（`run_in_docker.sh` + `llm_relay_proxy.py`）
- worktree 统一放在 EAR 根下 `../../ear-worktrees/`（容器挂载了整个 EAR，所以放这里容器能看见）

### 1. 开一个迭代版本（worktree + 分支）
```bash
cd mle-bench-agents/efficient-auto-research
bash scripts/new_iteration.sh <name> [base_ref]
#   <name>     迭代名，如 no-self-obs / stagnation-detect
#   [base_ref] 从哪分叉，默认当前 HEAD；想从干净 baseline 分叉就传对应 commit
```
产出：worktree 目录 `../../ear-worktrees/<name>`，新分支 `iter/<name>`。

### 2. 改代码 + 提交
```bash
cd ../../ear-worktrees/<name>
# 改 agent/engine/thompson.py 等
git add -A && git commit -m "iter/<name>: 说明这版改了什么、预期解决什么"
```
> 提交后 `git_dirty` 才是 False，report 里记的 commit 才真正对应跑的代码。**跑评测前务必先 commit。**

### 3. 跑评测（关键：EAR_AGENT_DIR 指向 worktree）
```bash
cd ../../docker-eval
EAR_AGENT_DIR=/mnt/sdc/shijianwang/efficient-agent-research/ear-worktrees/<name> \
RUN_TAG=<日期>_<name> \
  bash run_in_docker.sh efficient-auto-research <comp> <gpu> <steps> <timeout>

# 例：no-self-obs 版跑 chaii，12h，GPU0
EAR_AGENT_DIR=.../ear-worktrees/no-self-obs RUN_TAG=20260712_no-self-obs \
  bash run_in_docker.sh efficient-auto-research chaii-hindi-and-tamil-question-answering 0 9999 43200
```
- 不设 `EAR_AGENT_DIR` → 跑主工作目录（老行为，兼容）。
- 结果落在 **worktree 自己的** `docker_runs/<RUN_TAG>_<comp>/`，各版天然隔离、互不覆盖。
- `report.json` 会自动记录 `git_commit`/`git_branch`/`git_dirty`。
- 三题并发可参照 `docker-eval/launch_12h_3task_8gpu.sh`（给里面的 `run_in_docker.sh` 调用加 `EAR_AGENT_DIR` 即可切版本）。

**同时跑 baseline 对比**：再开一个 worktree（或直接用主目录）跑同一 comp，占不同 GPU，两份结果 tag 不同、目录不同，跑完直接比。

### 4. 看效果
```bash
# a) 版本对比表（steps / best / 首达best的步 / 时长 / token，含 git commit）
python mle-bench-agents/efficient-auto-research/scripts/compare_runs.py \
  ear-worktrees/<name>/docker_runs \
  mle-bench-agents/efficient-auto-research/docker_runs
#   --filter chaii   只看某题

# b) 官方评分（唯一权威 metric；report 里的 best_metric 是本地 holdout，不等于官方分）
source /mnt/sdc/shijianwang/miniconda3/etc/profile.d/conda.sh && conda activate mlebench
cd docker-eval
python grade.py <comp> ../ear-worktrees/<name>/docker_runs/<tag>_<comp>/submission.csv
# 若有 ensemble 侧产物，也评一下 workspace/ensemble_submission.csv
```
判断标准：**官方 grade 分 + 奖牌线**为准；`compare_runs.py` 用来看效率/停滞（best 是不是很早就达到、之后长期不动）。

### 5. 保留或回退
```bash
# 这版好 → 合回主分支
cd mle-bench-agents/efficient-auto-research
git checkout proxy-based-eval
git merge iter/<name>            # 或 cherry-pick 其中有价值的单个 commit

# 这版差 → 丢弃（回退就是删 worktree + 分支，主分支从未被动过）
git worktree remove --force ../../ear-worktrees/<name>
git branch -D iter/<name>
```
> baseline 一直在主分支上原封不动，所以"回退上一版"永远是零成本的——不改主分支、不用 revert。

## 反查："这个结果是哪版代码跑的？"
```bash
python -c "import json;r=json.load(open('<结果目录>/workspace/report.json'));print(r['git_commit'], r['git_branch'], 'dirty' if r['git_dirty'] else 'clean')"
git -C mle-bench-agents/efficient-auto-research show <commit> --stat
```
`git_dirty=True` 表示跑的时候有未提交改动 → 该结果**不可精确复现**，下次记得先 commit。

## 命名约定
- worktree/分支名：kebab-case，描述改动意图（`no-self-obs`、`stagnation-detect`、`ensemble-only`）
- `RUN_TAG`：`<日期>_<迭代名>`，如 `20260712_no-self-obs`，让 `docker_runs/` 目录一眼能对上是哪版

## 相关文件
| 文件 | 作用 |
|------|------|
| `scripts/new_iteration.sh` | 建迭代 worktree + 分支 |
| `scripts/compare_runs.py` | 汇总各 run 的 report.json 成对比表 |
| `agent/engine/search.py::_save_report` | 把 git commit/branch/dirty 写进 report.json |
| `../../docker-eval/run_in_docker.sh` | 评测入口，认 `EAR_AGENT_DIR` 覆盖代码目录 |
| `../../docker-eval/grade.py` | mlebench 官方评分 |
