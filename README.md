# Efficient Agent Research

本仓库研究 EAR 的 Agent 搜索效率，并维护 EAR 与六个 baseline 的统一 Benchmark Adapter。
本机进行 **MLE-Bench Lite 22 题**和 **Terminal-Bench AO 36 dev / 53 held-out** 两条评测。
MLE 比较七家，AO 比较 EAR、Arbor、Codex、Claude Code、AiScientist 五家。

当前实验已经开始，尚未完成；2026-09-20 核验时，MLEvolve 的三个 cell 和 v7 补跑仍暂停。
当前协议、模型与就绪状态统一见 [Adapter 主文档](BenchmarkAdapters/README.md)，
新建运行和暂停批次处理见[启动手册](BenchmarkAdapters/docs/CAMPAIGN_LAUNCH.md)，
源码身份见[版本记录](BenchmarkAdapters/docs/ON_DISK_AGENT_VERSIONS.md)。

- 本轮模型：`gpt-5.6-terra`，reasoning effort high、temperature 1.0；旧 gpt-5.5 结果单列。
- 本轮重复次数：N=1，MLE 每题 12h / AO 外层 48h；不是已经完成的 Avg@3 比较。
- 主机：256 vCPU / 251GB RAM / 8×RTX 4090；各 run 的实际配额以 manifest 为准。
- MLE 权威分数：官方 `mlebench grade_csv`；Agent 的本地 `best_metric` 不等于官方分。

下文第 1–7 节保留 G0–G5 历史研究脉络；其中六题、模型和算法代际的结果不能作为
当前 22 题正式横评。完整导航见第 9 节。

---

## 1. 这个项目在回答什么问题

MLE-Bench 是 OpenAI 的 Kaggle 式 ML 竞赛 agent 基准（arXiv:2410.07095）。主流打法（AIDE、
MLEvolve）靠**大量搜索 + 集成**堆奖牌率。我们的假设是：**用一个更有样本效率的搜索策略
（Kernel Thompson Sampling）能用少一两个数量级的 token / 步数，拿到相当的分数。**

所以每张结果表都有两个维度：**分数/奖牌**（效果）和 **token/步数**（效率）。EAR 的目标不是
只追求最高分，而是提高「**单位 token 的分数**」；历史 G0-G3 实验中该效率比约领先
MLEvolve 12-24 倍，G5 仍需独立验证。

---

## 2. 运行架构与历史基础设施

当前正式入口是 `BenchmarkAdapters` 的 `mle-cell` / `terminal-ao`，Agent 保留自己的
搜索循环，host 负责预算、产物、relay 与最终评分。MLE 的部分原生路径仍调用
`docker-eval/run_in_docker.sh`，因此该脚本不是废弃实现。

共享 host relay 与 per-run relay 的关系、协议转换和 token 记账统一见
[LLM relay 说明](BenchmarkAdapters/LLMRelay/README.md)。本机共享配置使用 6201，
v7 保存了 6202/6203 的独立 slot 配置；不能把历史 `6200 + GPU_ID` 示意当作当前固定端口表。

### 历史三 Agent 的代码状态（记录当时的代际）

| Agent | 分支 / 位置 | 说明 |
|-------|------------|------|
| **EAR** | `mle-bench-agents/efficient-auto-research`，分支 `ear/g3`，commit `7cd9ed5` | 当前两个正式 Benchmark 统一使用纯 G3；G4–G7 不进入正式 Adapter |
| MLEvolve | `baselines/MLEvolve` @ `main@fe92521` = 纯上游（`coldstart=False`） | 零 LLM 修改；历史侵入式改动存档在 `gpt55-local` 分支（勿用） |
| Arbor | `baselines/Arbor` | 当前活跃开源 baseline |

---

## 3. 历史六题选型

Low 3 + Medium 3（区分梯度 8/8 → 3/8，选题依据见
[mle-bench-research/01](mle-bench-research/01_benchmark_and_selection.md)）：

| 题目 | 竞赛 ID | 指标（方向） |
|------|---------|-------------|
| spooky-author | `spooky-author-identification` | multiclass log-loss（↓ 越低越好） |
| tweet-sentiment | `tweet-sentiment-extraction` | word-level Jaccard（↑） |
| essay-scoring | `learning-agency-lab-automated-essay-scoring-2` | QWK（↑） |
| jigsaw-toxic | `jigsaw-toxic-comment-classification-challenge` | mean column AUC（↑） |
| mlsp-birds | `mlsp-2013-birds` | AUC（↑） |
| chaii-qa | `chaii-hindi-and-tamil-question-answering` | Jaccard（↑） |

---

## 4. EAR 的算法演化（读这段就懂全部脉络）

EAR 的核心是把 agent 搜索建模成 **GP 回归 + 树上 Kernel Thompson Sampling**：一个节点的观测
（子节点 metric）经 cosine-kernel 传播到所有相似节点，从而用更少步数做出有信息的父节点选择。
算法白皮书见 [mle-bench-agents/efficient-auto-research/README.md](mle-bench-agents/efficient-auto-research/README.md)。

在此内核之上，2026-07 做了 G1–G5 迭代。每一代都可用 git commit 精确定位：

| 代 | 日期 | commit | 别名 | 改了什么（文件级） | 净效果 |
|----|------|--------|------|-------------------|--------|
| **G0** | 07-04 | `fee7df0` | **fair**（基线） | 删掉 mlsp 题目专属提示，做到与 MLEvolve 完全对称 | 干净对照组；三题皆无奖牌 |
| **G1** | 07-10 | `107e437` | **imp**（四项改进） | `search.py`(+141) `thompson.py`(+17)：①GP 自观测 ②top-K ensemble ③错误记忆聚合 ④改进模式 prompt | mlsp 摘 🥉（首枚 12h 奖牌）；**chaii 崩** 0.714→0.551 |
| **G2** | 07-13 | `1b386cd` | **stag**（停滞自升温） | `thompson.py`(+42) `search.py`(+45) `llm/__init__.py`：停滞自适应探索温度 T + 持久化缓存 prompt + 生成温度 0.7→1.0 | **修复 chaii 挖地**（→0.728 过 median）；mlsp 升 🥈 0.924 |
| **G3** | 07-15 | `5d1ca2a`+`7cd9ed5` | **metric-sign 修复** | `search.py`+`thompson.py`：搜索方向自适应（LLM 判定 metric 升/降），修 spooky 这类 log-loss（越低越好）题被当成越高越好的方向 bug；并修初始化顺序（token 计数器先于 LLM 探针） | spooky 校正为 🥈 0.215 |
| **G4** | 07-19 | `7be8152` | **verified outcomes 实验** | 检测 no-op/duplicate prediction 并改变搜索资格、best/ensemble 行为 | 六题统一运行出现明显退步；策略已在 `212870f` 撤销 |
| **G5** | 07-22 | `a6acc90` | **attempt isolation + telemetry** | 恢复 G3 搜索资格；每 attempt/run 隔离、冻结 artifact、完整 provenance、只读 behavior telemetry | 基础设施 closeout；**尚无 G5 官方分数** |

> **为什么保留 fair 而不是只看最新版？** imp 是「加了功能」不是「每题都更强」——它治好了 mlsp
> 却让 chaii 崩掉。fair 是唯一与 MLEvolve **同轮同条件**的干净 A/B 对照，回答「同等条件谁强」
> 只能用它。G2/G3 才把 imp 的副作用逐一修掉。G4/G5 的失败与基础设施归因见
> [mle-bench-research/07](mle-bench-research/07_g4_failure_and_g5_infrastructure.md)。

### G2 停滞自升温 TS —— 历史算法层关键改进

imp 的 chaii 退步根因：**GP 自观测把 TS 拉向当前 best，探索方差被压缩 4 倍（0.041→0.011），
搜索锁死在 0.55 低分盆地**。G2 的解法不是回滚，而是加一个纯搜索层旋钮：

```
best 连续停滞 > 3 步  →  给 TS 后验采样方差乘温度 T（每步 +0.5，上限 3.0 = fair 的高方差状态）
best 一改善        →  T 立刻回到 1（行为与改进前完全一致）
```

不动 LLM 温度，只放大 TS 采样方差让它「跳」出盆地。实测父节点多样性从 4/10 → 10/10。
同一个锁死病 mlsp 也有（88 步里 42% 卡在 0.73 盆地），所以 mlsp 顺带从 🥉0.887 升到 🥈0.924。

---

## 5. 结果速览（6 题 × 两方，官方 mlebench 评分）

口径：以下是 **G0–G3 历史结果**，EAR / MLEvolve（我们跑）均取当时 best 与
ensemble/fusion 中更优者。它们不能作为 G5 分数。MLEvolve 官方 trace = OpenAI mle-bench 仓库
`runs/mlevolve_group{1,2,3}/`（Gemini-3-Pro-preview, 12h, 1×H200, 3 seeds，即 leaderboard #4 =
61.33% 那次），三个数字为 seed1/2/3。

| 题目 | EAR（我们，含版本） | MLEvolve（我们跑） | MLEvolve 官方 trace（3 seeds） |
|------|--------------------|-------------------|-------------------------------|
| spooky-author ↓ | **0.2150 🥈**（G3） | 0.2463 🥈 | 0.197 / 0.224 / 0.222 · 银×3 |
| tweet-sentiment ↑ | **0.71899 🥈**（新3题） | 0.71354 ⬜ | 0.71765🥈 / 0.71732🥉 / 0.71574 |
| essay-scoring ↑ | **0.83724 🥇**（新3题） | 0.83570 🥈 | 金×3（0.837~0.839） |
| jigsaw-toxic ↑ | 0.98297 ⬜（fair）/ 0.98236（imp） | 0.98231 ⬜ | 金×2 银×1（0.987~0.988） |
| mlsp-birds ↑ | **0.92431 🥈**（G2）↑ from imp 🥉0.887 | **0.92949 🥈** | 金×2 银×1（0.935~0.951） |
| chaii-qa ↑ | **0.72768 ⬜过median**（G2）↑ from imp 0.551 | 0.72932 ⬜ | 银×1 + 2×无奖牌（0.673~0.759） |

图例：🥇金 🥈银 🥉铜 ⬜过median无奖牌 · ↑越高越好 ↓越低越好

**EAR 六题奖牌合计（取各题最好版本）：1 金（essay）+ 3 银（spooky/tweet/mlsp）= 4 枚**，
jigsaw/chaii 过 median 无牌。**关键对比**：官方满血 MLEvolve（Gemini-3-Pro）很强，但我们用
gpt-5.5 复现的 MLEvolve 明显弱于官方；**EAR 在同一 gpt-5.5 口径下反而经常追平官方 MLEvolve**
（essay 同为金、spooky 落在官方银牌区间、tweet 高于官方最好 seed）。

### 效率（token）

| 轮次 | EAR 合计 | MLEvolve 合计 | 倍数 |
|------|---------:|--------------:|------|
| jigsaw/mlsp/chaii 公平轮（fair） | 236 万 | 5609 万 | **23.7×** |
| 改进轮（imp，仅 EAR） | 443 万 | —（沿用对照） | ~8× |

单题 token（新链路，report.json）：spooky 71万 / tweet 72万 / essay 129万 / chaii(G2) 54万 /
mlsp(G2) 286万。

---

## 6. 目录与「最新文件在哪」

```
efficient-agent-research/
├── README.md                    # 本文件 = 项目权威入口
├── BenchmarkAdapters/LLMRelay/  # 七 Agent、五 Benchmark 共用的 host-owned LLM 中继
├── docker-eval/                 # MLE Docker 评测框架：run_in_docker.sh + grade.py
├── mle-bench/                   # OpenAI mlebench 框架 (pip editable)
│   └── runs/mlevolve_group{1,2,3}/grading_report_group_*.json  # ★ MLEvolve 官方逐题 trace
├── mle-bench-data/              # MLE-Bench Lite prepared 数据
├── mle-bench-agents/            # EAR 源码
│   └── efficient-auto-research/ # 当前 Adapter 使用的 EAR G3 checkout
├── baselines/                   # Arbor、MLEvolve、Codex、Claude、ML-Master、AiScientist
├── ear-worktrees/
│   ├── stagnation-cache/        # 历史 G2/G3 benchmark 产物
│   └── attempt-isolation-telemetry-v2/ # G5/G6 历史基础设施 worktree；当前为 ear/g6
│       └── docker_runs/<tag>_<comp>/
│           ├── launch_manifest.json
│           ├── submission.csv
│           └── workspace/runs/<run_id>/{report.json,traces,attempts,artifacts}
├── mle-bench-research/          # 研究综述文档 01–07（07 最新）
├── run-logs/                    # 每轮评测报告 + token 日志
├── analysis/                    # 诊断存档与对比图
└── cache/                       # HF 模型缓存
```

**历史基础设施代码**：`ear-worktrees/attempt-isolation-telemetry-v2` 当前在 `ear/g6`；下述 G5
指历史 commit，而不是该目录今天的 HEAD。G5
行为基线 `a6acc90` 从 `212870f`（撤销 G4 行为过滤）出发，只增加 attempt/run 隔离、artifact
完整性和 telemetry；`thompson.py` 保持 G3 byte-identical。当前统一 Adapter 选用的 EAR
source 是上面的 G3 checkout；下面列出的分数仍是历史版本结果，不能视作当前 Adapter 的新结果。

**最新已完成的历史结果产物**（都在 `ear-worktrees/stagnation-cache/docker_runs/`）：

| 题目/版本 | 目录 |
|-----------|------|
| chaii（G2 自升温） | `20260713_stagcache_chaii-hindi-and-tamil-question-answering/` |
| mlsp（G2 自升温） | `20260713_stagcache_mlsp-2013-birds/` |
| spooky/tweet/essay（新3题） | `20260714_3newtasks_<comp>/` |
| spooky（G3 metric-sign 修复重跑） | `20260715_dirfix_spooky-author-identification/` |

token 日志同名 `run-logs/20260713_stagcache_token_usage/`、`20260714_3newtasks_token_usage/`、
`20260715_dirfix_token_usage/`。前三题（jigsaw/mlsp/chaii）fair/imp 版产物在
`mle-bench-agents/efficient-auto-research/docker_runs/20260704_12h_fair_*` 与
`20260710_12h_ear_improved_*`。

**研究文档**（`mle-bench-research/`，读序）：
01 选题 → 02 环境/agent 配置 → 03 早期短跑（Sonnet 时代）→ 04 12h 长跑归因 →
05 公平对比+四项改进 → 06 停滞自升温+全 6 题+官方 trace →
**07 G4 失败、G5 基础设施与当前实验协议（最新）**。

---

## 7. 历史 G5 运行示例

```bash
cd docker-eval
# bash run_in_docker.sh <agent> <competition> <gpu_id> [steps] [timeout_sec]

# 跑 G5 需显式提供上游 credential，并指定 worktree：
OPENAI_API_KEY=... \
EAR_AGENT_DIR=$PWD/../ear-worktrees/attempt-isolation-telemetry-v2 RUN_TAG=my_isolated_run \
  bash run_in_docker.sh efficient-auto-research chaii-hindi-and-tamil-question-answering 0 9999 43200

# 官方评分：
source /mnt/sdc/shijianwang/miniconda3/etc/profile.d/conda.sh && conda activate mlebench
python grade.py <comp> <path/to/submission.csv>
```

历史迭代、架构和 G5 协议文档位于主实验工作区的
`ear-worktrees/attempt-isolation-telemetry-v2/docs/`，文件名分别为 `ITERATION.md`、
`ARCHITECTURE.md`、`EXPERIMENT_PROTOCOL.md`。这些本地 worktree 不随外层 Git 分发，
复现时需同时确认它们的路径和历史 commit。

---

## 8. 当前后续工作

1. 按暂停收据核对现有进程、预算和原配置，再安排未完成 cell；入口见启动手册。
2. 保留失败、重试、评分和 token 记录，按协议、模型、硬件和 adapter commit 分组核验。
3. 核查 Arbor 的来源记录：MLE manifest 使用 registry 的 `baselines/Arbor` 身份，Docker
   实际默认快照来自 `baselines/Arbor-longrun-patched`；两棵树必须同时追溯，不能当作重复目录删除。
4. 后续算法代际或 N=3 比较独立冻结协议，不将历史最好分拼成当前结果。

## 9. 文档导航

| 内容 | 入口 |
|---|---|
| 当前状态、比较集合、架构和计分规则 | [BenchmarkAdapters](BenchmarkAdapters/README.md) |
| 新建运行、暂停批次、preflight、重试和聚合 | [运行手册](BenchmarkAdapters/docs/CAMPAIGN_LAUNCH.md) |
| 当前源码 pin 与历史身份 | [Agent 版本记录](BenchmarkAdapters/docs/ON_DISK_AGENT_VERSIONS.md) |
| 各 Agent 在两个 Benchmark 上的接入差异 | [逐格文档](BenchmarkAdapters/docs/adapters/README.md) |
| 原生 Docker runner、镜像、挂载和历史 launcher | [docker-eval](docker-eval/README.md) |
| 依赖环境安装 | [UV 环境矩阵](BenchmarkAdapters/environments/README.md) |
| 历史研究、六题结果与算法演化 | [研究档案](mle-bench-research/README.md) |
| Benchmark / Baseline 上游调研资料 | [基准与 Baseline 调研](baselines/README.md) |
| 其他 Benchmark 与修复历史 | [文档索引](BenchmarkAdapters/docs/README.md) |

根仓库保留源码、任务定义、协议和依赖锁文件；数据、权重、虚拟环境、运行目录和
大部分 `analysis/` 产物由 `.gitignore` 排除。当前运行入口及原始实验记录仍需在本机
保全，不能仅以是否受 Git 跟踪判断能否清理。
