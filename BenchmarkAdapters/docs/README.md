# Benchmark Adapter 文档索引

当前能力、就绪状态、公平边界、最终候选选择和计分规则统一在
[BenchmarkAdapters README](../README.md)。本页只维护导航，避免重复的状态表过时。

| 需要查什么 | 文档 |
|---|---|
| 当前两个 Benchmark 的操作步骤、暂停批次与新建运行的区别 | [启动手册](CAMPAIGN_LAUNCH.md) |
| 当前源码 pin 与 8 月历史快照 | [Agent 版本记录](ON_DISK_AGENT_VERSIONS.md) |
| 各 Agent 如何接 MLE / AO、哪些组合不适用 | [逐格适配索引](adapters/README.md) |
| 模型协议、输出预算、token 记账 | [LLM relay](../LLMRelay/README.md) |
| 密钥池配置与运行期间的选择规则 | [Relay key pool](RELAY_KEY_POOL.md) |
| UV 环境安装及 runtime profile | [环境矩阵](../environments/README.md) |
| 历史修复编号、设计依据与验收条件 | [双 Benchmark 修复规范](SEVEN_AGENT_BENCHMARK_REPAIR_PLAN.md) |
| Arbor 的 artifact/评分边界修复 | [Arbor MLE repair](ARBOR_MLE_ADAPTER_REPAIR.md) |

| 其他 Benchmark | 独立协议与部署说明 |
|---|---|
| AutoResearch Architecture Design | [七 Agent 适配计划](AUTORESEARCH_SEVEN_AGENT_ADAPTER_PLAN.md) |
| modded-NanoGPT Optimizer Design | [适配与 baseline gate](OPTIMIZER_DESIGN_SEVEN_AGENT_ADAPTER.md) |
| FML-Bench | [适配与证据要求](FML_SEVEN_AGENT_ADAPTER.md) |

历史 `IMPLEMENTATION.md` 的实现、selection 和统计内容已合并至上级 README；
早期 `relay-smoke.md` 的传输诊断记录已并入 LLM relay 说明。完整结果保留在各自
campaign / run-log 中，不复制成新的“当前成绩表”。
