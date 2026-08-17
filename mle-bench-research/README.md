# MLE-Bench Agent 调研与评测

本目录记录 EAR（Kernel Thompson Sampling）与开源 baseline 在 MLE-Bench 上的
选题、环境、公平性、历史实验、失败归因和 G5 实验基础设施。它是研究档案，不是当前
七 Agent × 两个目标 Benchmark 的正式成绩表。

## 当前状态

| 项目 | 当前值 |
|---|---|
| 研究档案记录到 | G5 |
| 历史基础设施分支 | `ear/g5` |
| 当前统一 Adapter 的 EAR source | `mle-bench-agents/efficient-auto-research@7cd9ed5`（G3） |
| 行为基线 | `a6acc90` |
| 搜索策略 | G3-compatible KTS；G4 行为过滤已撤销 |
| 基础设施 | attempt/run 隔离、artifact 完整性、provenance、只读 telemetry |
| 当前七 Agent × 两个目标 Benchmark 正式结果 | 暂无；协议资产、依赖、clean source 和真实 scored smoke 尚未全部完成 |

历史 G0–G3 分数不能写成 G5 分数。G5 正式比较必须使用同一 frozen commit 完整运行，
再对最终 submission 做官方 mlebench grading。

## 文档索引

| 文档 | 内容 | 时效 |
|---|---|---|
| [01 benchmark and selection](01_benchmark_and_selection.md) | MLE-Bench 概况、评测机制、历史排行榜快照、6 题选型 | 选题现行；排行榜是历史快照 |
| [02 setup and agents](02_setup_and_agents.md) | 环境、数据、relay、活跃 Agent 配置与公平性口径 | 已同步 G5 |
| [03 experiments and findings](03_experiments_and_findings.md) | Sonnet 时代短跑和 trace 归因 | 历史 |
| [04 efficiency analysis](04_mlevolve_vs_efficient_efficiency.md) | 早期 12h 长跑、MLEvolve 效率优势、执行门禁优先级 | 历史 |
| [05 fair comparison](05_fair_comparison_and_ear_improvements.md) | 公平审计、G0/G1 三轮 12h 对比、mlsp/chaii 归因 | 历史至 G1 |
| [06 G2/G3 and six tasks](06_stagnation_heated_ts_and_full_six_tasks.md) | G2/G3 算法修复、历史六题 best-of-versions、官方 MLEvolve trace | 历史至 G3 |
| [07 G4 failure and G5 infrastructure](07_g4_failure_and_g5_infrastructure.md) | G4 失败、G5 设计、可信链、当前协议和剩余工作 | **当前** |

## 固定研究对象

六题：spooky-author、tweet-sentiment、essay-scoring、jigsaw-toxic、mlsp-birds、chaii-qa。

Baseline：

| Agent | 方法 | 本地代码状态 |
|---|---|---|
| EAR | Exact GP + Kernel Thompson Sampling | 当前 Adapter：G3 `7cd9ed5`；G5 仅为历史基础设施 |
| MLEvolve | Monte Carlo Graph Search + UCT | `main` 纯上游 @ `fe92521`；`coldstart=False` |
| Arbor | 开源 baseline | `baselines/Arbor` |

当前统一 LLM 链路是 gpt-5.5 + host relay proxy，硬件是 8×RTX 4090、每任务独占
21 CPU。早期 03/04 的实验使用 Claude Sonnet 4.6，不能与当前运行直接横比。

## 历史结果如何使用

文档六汇总的“1 金 3 银”是跨 G0–G3 取每题历史最好版本的诊断性汇总，证明过往
算法曾达到这些水平，但不是一个单版本 benchmark。它可以支持研究方向判断，不能作为
G5 主实验表。

固定 MLEvolve 本地六题官方分数仍可作为 baseline 对照，但只有取得 G5 的六题官方分数后
才能同口径计算差值和胜负。

## 当前下一步

1. G5 最小端到端 smoke：launcher、Docker、relay、GPU、manifest、report、final hash。
2. 确认历史硬编码 credential 已在服务端撤销。
3. 从一个干净的 G5 commit 完整跑六题，不拼接历史分数。
4. 完成官方评分后，再决定是否进入算法层 G6 实验。

运行细节见 [docker-eval README](../docker-eval/README.md)，G5 的完整协议见
[EXPERIMENT_PROTOCOL](../ear-worktrees/attempt-isolation-telemetry-v2/docs/EXPERIMENT_PROTOCOL.md)。
