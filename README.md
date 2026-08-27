# Efficient Agent Research

本仓库包含 EAR 的历史研究，以及七个 Agent 的统一 Benchmark Adapter。

当前正式评测目标是 **MLE-Bench Lite 22 题**和
`terminal-bench-ao-reconstruction-v1` 的 **36 dev / 53 held-out** AO 评测，覆盖 EAR、
MLEvolve、Arbor、Codex、Claude Code、ML-Master 2.0 和 AiScientist。Adapter 的代码入口、
原生 launcher、评分器和聚合器已经写入仓库，但当前还没有完成正式运行所需的协议文件、
模型配置、运行环境整理和真实 scored smoke，因此不能把当前状态写成“已经有七 Agent
正式横向分数”。准确状态见
[`BenchmarkAdapters/docs/SEVEN_AGENT_BENCHMARK_REPAIR_PLAN.md`](BenchmarkAdapters/docs/SEVEN_AGENT_BENCHMARK_REPAIR_PLAN.md)。

下面的 EAR/MLEvolve 六题内容属于历史研究，不是当前 22 题 × 7 Agent 的正式结果。
当前 EAR 代码基准为 `mle-bench-agents/efficient-auto-research@ear/g3@7cd9ed5`；G4-G7
实验分支仍保留作历史记录。七家本轮**不追上游**，按 2026-08-27 磁盘 checkout 跑
（含未提交补丁）；身份表见
[`BenchmarkAdapters/docs/ON_DISK_AGENT_VERSIONS.md`](BenchmarkAdapters/docs/ON_DISK_AGENT_VERSIONS.md)。

- **LLM：** gpt-5.5（relay 端点，reasoning_effort=high），经本地转发代理统一接入
- **硬件：** 256 vCPU / 251GB RAM / 8× RTX 4090
- **权威评分：** 一律用官方 `mlebench grade_csv`（`docker-eval/grade.py`），report.json 里的
  `best_metric` 只是本地 holdout，**不等于**官方分

---

## 1. 这个项目在回答什么问题

MLE-Bench 是 OpenAI 的 Kaggle 式 ML 竞赛 agent 基准（arXiv:2410.07095）。主流打法（AIDE、
MLEvolve）靠**大量搜索 + 集成**堆奖牌率。我们的假设是：**用一个更有样本效率的搜索策略
（Kernel Thompson Sampling）能用少一两个数量级的 token / 步数，拿到相当的分数。**

所以每张结果表都有两个维度：**分数/奖牌**（效果）和 **token/步数**（效率）。EAR 的目标不是
只追求最高分，而是提高「**单位 token 的分数**」；历史 G0-G3 实验中该效率比约领先
MLEvolve 12-24 倍，G5 仍需独立验证。

---

## 2. 架构：纯上游零侵入 + 本地转发代理

历史 MLE 实验主要使用 **EAR、纯原版 MLEvolve、Arbor**。当前七 Agent 正式 Adapter
统一通过本地转发代理接入模型；各 Agent 的源码版本、variant 和运行状态必须以
`BenchmarkAdapters/registry.py` 与正式 preflight 输出为准：

```
EAR / MLEvolve / Arbor
    │  OPENAI_BASE_URL → http://127.0.0.1:620X/v1   (端口 = 6200 + GPU_ID，每容器一个实例)
    ▼
BenchmarkAdapters/LLMRelay/server.py
    │  模型重写→gpt-5.5 · 强制 reasoning_effort=high · 剥 max_tokens 等参数
    │  重试20次 · 不限超时 · 非流式化+SSE合成 · tool-call JSON兜底 · system-only消息归一化
    │  token 独立记账 → run-logs/<TAG>_token_usage/<agent>_<comp>_gpu<N>.jsonl
    ▼
relay 上游 (gpt-5.5)
```

价值：换模型只改环境变量；token 记账覆盖各 Agent 的调用；代理重试在全部实验中兜住上游抖动
（合计 60+ 次重试，零 agent 层失败）。详见 [docker-eval/README.md](docker-eval/README.md)。

**cache token 记账（2026-08-27 修复）**：上游 `gpt-5.6-terra` 两个端点报的 usage 形状不同——
`/v1/chat/completions` 把命中数放在 `usage.prompt_tokens_details.cached_tokens`（实测长 prompt
重复请求：`prompt_tokens=11207`，其中 `cached_tokens=11008`），`/v1/responses` 则放在
`usage.input_tokens_details.{cached_tokens,cache_write_tokens}`、reasoning 放在
`output_tokens_details.reasoning_tokens`。原 `_append_token_log` 只读 `prompt_tokens_details` /
`completion_tokens_details`，且要求 Anthropic 与 OpenAI 三个字段**同时**非空才算 `cache_tokens`，
结果所有真实命中都被写成 `cache_tokens=None`（387 条 host 日志里 111 条有 `cached_tokens`、
0 条有 `cache_tokens`），并让 `summarize_token_log` 把整段 cache 汇总降级为 None。现改为按端点
回退取值、只要有任一 cache 信号就求和；上游完全不报时仍为 None（保持「未知」而非伪造 0）。
`total_tokens = input + output` 保持不变且**正确**：OpenAI 约定里 `cached_tokens` 是
`prompt_tokens` 的子集而非增量（实测 11207+5=11212=上游 `total_tokens`），加上去会重复计数。

### 历史三 Agent 的代码状态

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
│   └── attempt-isolation-telemetry-v2/ # ★ G5（ear/g5；尚未正式 benchmark）
│       └── docker_runs/<tag>_<comp>/
│           ├── launch_manifest.json
│           ├── submission.csv
│           └── workspace/runs/<run_id>/{report.json,traces,attempts,artifacts}
├── mle-bench-research/          # 研究综述文档 01–07（07 最新）
├── run-logs/                    # 每轮评测报告 + token 日志
├── analysis/                    # 诊断存档与对比图
└── cache/                       # HF 模型缓存
```

**历史基础设施代码**：`ear-worktrees/attempt-isolation-telemetry-v2`，分支 `ear/g5`。G5
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

## 7. 快速开始

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

迭代工作流（新建 worktree、版本可追溯、compare_runs）见
[G5 ITERATION](ear-worktrees/attempt-isolation-telemetry-v2/docs/ITERATION.md)。完整架构和实验口径见
[ARCHITECTURE](ear-worktrees/attempt-isolation-telemetry-v2/docs/ARCHITECTURE.md) 与
[EXPERIMENT_PROTOCOL](ear-worktrees/attempt-isolation-telemetry-v2/docs/EXPERIMENT_PROTOCOL.md)。

---

## 8. 下一步

1. **先做 G5 最小端到端 smoke**：验证 launcher、Docker、relay、GPU、manifest、report 和
   final hash 闭环；closeout 只做了 mock/单元测试，没有真实启动。
2. **确认旧 credential 已服务端撤销，并版本化宿主工具**：当前 launcher/relay/plot 在 EAR Git
   仓库外，只能依靠运行 manifest 的 SHA-256 追踪。
3. **再做 G5 全 6 题统一复跑**：必须来自同一 frozen commit，不得用 G0–G3 历史最优拼接。
4. **mlsp 仍输官方满血 MLEvolve 的方案档次**（历史 G2 0.924 vs 本地 MLEvolve 0.929/官方 0.95）：EAR 的
   「复杂度纪律」变成天花板，写不出 7000 行的重型 pipeline。下一步：停滞时**双解锁**——
   放大探索方差（已做，治 chaii）+ 解锁重型方案档 prompt（待做，治 mlsp）。
5. MLEvolve 复现分数远低于官方，需确认是否纯 LLM 差异（gpt-5.5 vs Gemini-3-Pro）。
```

---

## 9. Baseline、Benchmark Adapter 与 UV 环境

本仓库的 `full` 分支把 Baseline 源码、MLE-Bench / Terminal-Bench 源码、共享
Adapter 和可复现的 UV 配置放在同一个集成树中。Adapter 的唯一入口是
[`BenchmarkAdapters/`](BenchmarkAdapters/)，其中按 Benchmark 分为
`MLEBenchLite/` 与 `TerminalBench/`，公共进程、Relay、Agent registry 和 CLI
位于其根目录。

此外，`autoresearch/` 保存了 Architecture Design Benchmark 的固定上游源码快照
`karpathy/autoresearch@228791f`、原始 `uv.lock` 和本项目部署脚本。共享 Benchmark
Adapter、七 Agent 原生 bridge、held-out evaluator 和聚合命令已经实现；真实 smoke 与
`7×3×48h` 正式 campaign 仍是独立验收门槛，因此不能把“命令可以构造”写成“正式协议
已经跑通”。架构、评分有效性、held-out 复测、测试矩阵和完成条件见
[`AUTORESEARCH_SEVEN_AGENT_ADAPTER_PLAN.md`](BenchmarkAdapters/docs/AUTORESEARCH_SEVEN_AGENT_ADAPTER_PLAN.md)。

`optimizer-design/` 进一步复用上述架构，冻结
`modded-nanogpt@bc1b58e` 的 Track 3 Optimizer Design，并采用“Benchmark 公共层 + 七个
Agent 小 Adapter”的两层结构。当前命令和 contract 已写入，但正式运行仍需要先完成双
held-out baseline 记录；详见
[`OPTIMIZER_DESIGN_SEVEN_AGENT_ADAPTER.md`](BenchmarkAdapters/docs/OPTIMIZER_DESIGN_SEVEN_AGENT_ADAPTER.md)。

Terminal-Bench 现在有两条不同路径：`terminal-direct-smoke` 是 89 题直接解题路径，
只能做基础设施检查；正式横向比较使用
`terminal-bench-ao-reconstruction-v1`，由外层 Agent 在 36 个 dev task 上优化同一份
`terminus-2`，最后只评测一次 53 个 held-out task。两条路径的分数不能混用。

当前 registry 已登记七个 Agent，但默认入口并不等于每个 Agent 都已经可以正式评分：
Arbor 的 MLE 需要 `arbor-benchmark-patched`，Terminal AO 的 AiScientist 需要
`ai-scientist-terminal-variant`。MLEvolve 和 ML-Master 2.0 **不参与** Terminal AO
（任务形状不匹配）。模型配置、协议资产、Python 依赖、clean source 和真实 smoke
全部完成后，才可以开始正式 campaign。后面怎么起实验（比较集合、冻结资产、
formal-preflight、smoke、22×7×3 MLE 与 5×3×48h AO）见
[`SEVEN_AGENT_BENCHMARK_REPAIR_PLAN.md` 第 17 节](BenchmarkAdapters/docs/SEVEN_AGENT_BENCHMARK_REPAIR_PLAN.md)。

已修复的四类正确性问题（2026-08-26，细节见各自文档）：

- **ML-Master 的 `is_lower_better`**：per-run config 以前沿用上游模板里写死的 `false`，
  在 22 题中的 7 道 lower-is-better 题上把最差解留在 `best_submission/`。方向现在由
  host 侧 `MLEBenchLite/metric_direction_worker.py` 从官方 leaderboard 解出后显式传入。
- **Arbor 内层仓库**：`executor_timeout` 4h→2h 的改动过去只提交到外层仓库，
  内层 `baselines/Arbor-longrun-patched` 的 dirty 子树让 launcher 直接 `exit 2`。
  该改动已提交到内层仓库；死变量 `ARBOR_SOURCE_ALLOW_DIRTY` 已删除。
- **relay 截断信号**：chat / responses / messages 三个协议互转时会把上游的
  `incomplete` 压成 `stop`/`end_turn`，让 Agent 把被截断的回答当成完整回答。
  三个方向现在都透传 `length` / `max_tokens` / `incomplete_details`。
- **95% CI 用 z 而非 t**：n=3 时用 z=1.96 把区间宽度低估约 2.2 倍，现在按
  `outer_repetitions - 1` 取 Student-t 临界值（df=2 时 4.3027）。

另有一处**尚未修复**的 provenance 错配：run manifest 记录 `baselines/Arbor` 的 commit，
launcher 实际跑的是 `baselines/Arbor-longrun-patched`，两者不是同一份代码。修复需要改
`registry.py`，见 `BenchmarkAdapters/docs/ARBOR_MLE_ADAPTER_REPAIR.md`。

每个 Agent 的差异说明位于对应目录的 `adapter_docs/`；环境安装清单与一键脚本位于
[`BenchmarkAdapters/environments/`](BenchmarkAdapters/environments/)：

```bash
bash BenchmarkAdapters/environments/install.sh --list
bash BenchmarkAdapters/environments/install.sh autoresearch
bash BenchmarkAdapters/environments/install.sh all
```

根仓库不会提交本机数据集、模型权重、虚拟环境、UV 缓存、Docker 运行目录、任务运行
记录或 token 日志。这些边界由根目录 `.gitignore` 统一管理；Benchmark 的 Python 源码、
任务定义、`pyproject.toml`、`uv.lock` 和安装清单会保留在 Git 中。API key 只通过运行时
环境变量提供，不写入仓库。
