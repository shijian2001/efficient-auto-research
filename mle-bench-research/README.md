# MLE-Bench Agent 研究档案

本目录保留 EAR 与 baseline 的历史选题、六题实验、效率分析、G0–G5 演化和失败归因。
它不是当前 22 题横评的操作手册。当前状态见
[Adapter 主文档](../BenchmarkAdapters/README.md)，启动/续跑边界见
[运行手册](../BenchmarkAdapters/docs/CAMPAIGN_LAUNCH.md)，源码 pin 见
[版本记录](../BenchmarkAdapters/docs/ON_DISK_AGENT_VERSIONS.md)。

## 文档索引

| 文档 | 历史内容 |
|---|---|
| [01 benchmark and selection](01_benchmark_and_selection.md) | Benchmark 概况、当时排行榜与六题选型 |
| [02 setup and agents](02_setup_and_agents.md) | 旧六题环境、relay、Agent 配置与公平口径 |
| [03 experiments and findings](03_experiments_and_findings.md) | Sonnet 时代短跑和 trace 归因 |
| [04 efficiency analysis](04_mlevolve_vs_efficient_efficiency.md) | 早期 12h 长跑、效率差距和执行门禁 |
| [05 fair comparison](05_fair_comparison_and_ear_improvements.md) | G0/G1 公平对比及算法改进 |
| [06 G2/G3 and six tasks](06_stagnation_heated_ts_and_full_six_tasks.md) | 停滞升温、metric-sign 修复与六题最好版本汇总 |
| [07 G4 failure and G5 infrastructure](07_g4_failure_and_g5_infrastructure.md) | G4 失败、G5 隔离/产物可信链设计与当时后续计划 |

## 使用这些记录时的口径

- 六题为 spooky-author、tweet-sentiment、essay-scoring、jigsaw-toxic、mlsp-birds、chaii-qa；
  它们不是当前完整 22 题集合。
- 早期 Sonnet 4.6、历史 gpt-5.5 与本轮 gpt-5.6-terra 分开比较，硬件和预算也需一致。
- “1 金 3 银”来自跨 G0–G3 取各题历史最好版本，是诊断汇总，不是单一版本的正式结果。
- 当前正式 EAR 仍使用 G3 代际；G5/G6/G7 的目录和文中“下一步”表示历史研究阶段，
  不替代当前 campaign 计划。`attempt-isolation-telemetry-v2` 目录目前在 `ear/g6`，
  需要复现 G5 时按文中历史 commit 定位，不能仅靠目录名。
- 部分旧 MLEvolve runs 已于 9 月清理。现有历史评分/说明保留在本机
  `analysis/july-2026-retained-reports/` 与 `run-logs/`；这些路径受 Git 忽略，
  文中原始 run 路径不代表完整旧权重仍在本地。

本目录的历史结论与原始上下文继续保留；运行命令、当前完成状态和模型配置只在统一
运行文档中维护。
