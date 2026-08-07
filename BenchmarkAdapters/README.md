# BenchmarkAdapters

`BenchmarkAdapters/` 是七个 Agent 在两个正式 Benchmark 上的唯一 canonical
适配层。小写 `benchmark_adapters/` 只做兼容性 re-export。

## 正式模式

- `mle` / `mle-cell`：冻结的 22 题 MLE-Bench Lite；最终 `submission.csv`
  由 host-owned 官方 `mlebench.grade.grade_csv` 评分。
- `terminal-ao`：`terminal-bench-ao-reconstruction-v1`；外层 Agent 在 48 小时内
  只使用 36-task dev broker 优化同一 `terminus-2`，冻结后一次性运行 53-task
  held-out test。
- `terminal-direct-smoke`：89 题直接解题基础设施 smoke，明确
  `non_comparable_to_terminal_ao=true`，不得进入 AO 榜单。

## 七 Agent

| Agent | MLE 原生路径 | Terminal AO 原生路径 |
|---|---|---|
| EAR | EAR Docker graph search | EAR KTS/Thompson repository backend |
| MLEvolve | MLEvolve Docker search/fusion | `AgentSearch`/`SearchNode` UCT repository backend |
| Arbor | native Arbor MLE runner | native Arbor coordinator |
| Codex | `codex exec` public-only workspace | native `codex exec` repository loop |
| Claude Code | `claude --print` public-only workspace | native `claude --print` repository loop |
| ML-Master 2.0 | generated per-run config + native workflow | EvoMaster native Agent repository workflow |
| AiScientist | native `aisci mle run` | native `TerminalTaskSubagent.run` |

## 公平性与计分

- 正式 run 写不可覆盖的 protocol、manifest、result、artifact hash 和 grader/evaluator
  JSON；dirty source 会被拒绝。
- MLE Agent 只见当前 prepared public 数据；private grader 留在 host。
- AO launcher 在 Bubblewrap 中只见 candidate、自己的 locked runtime、dev Unix
  capability 和 host relay socket；split、89-task dataset、held-out IDs 与 host credential
  不挂载。
- AO dev evaluation 在 disposable copy 上运行；revision 只允许修改冻结 allowlist；
  test endpoint 在搜索期不存在。
- MLE 和 Terminal AO 分栏统计，不生成未经预注册的混合总分。
- 这些边界只服务于公平横向比较；本实现不增加针对恶意 Agent 的额外防作弊系统。

## 命令

```bash
# 稳定协议/readiness JSON
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters status
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters preflight

# 生成 MLE 正式协议并运行一个 cell
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters mle-protocol \
  --output /runs/mle/protocol.json
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters mle-cell \
  --protocol /runs/mle/protocol.json --agent codex \
  --competition-id spooky-author-identification --seed 0 \
  --data-root /data/mle-bench --campaign-dir /runs/mle

# 运行一个 Terminal AO seed；正式 timeout 固定为 172800 秒
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters terminal-ao \
  --agent ear --protocol terminal-bench-2/ao_protocol/protocol.json \
  --output-dir /runs/ao/ear/seed-0 --seed 0

# 分别聚合
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters mle-aggregate \
  --protocol /runs/mle/protocol.json --campaign-dir /runs/mle --agent ear
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters terminal-ao-aggregate \
  --protocol terminal-bench-2/ao_protocol/protocol.json \
  --campaign-dir /runs/ao --agent ear
```

## Readiness 含义

`source_ready`、`environment_ready`、`command_ready`、`real_smoke_ready`、
`formal_protocol_ready` 是逐层证据，不是同义词。当前代码和 contract/synthetic 测试
完整测试 81 项通过，最终公平参数修复后定向回归 35 项通过，只能证明 command path；
两次 MLE 和两次 Terminal AO 正式入口均因当前
dirty worktree 结构化拒绝。只有归档真实 smoke/formal evidence 且 source clean 后，status
才允许显示更高层级，也才可发布正式横向比分。完整修复和验收清单见
`BenchmarkAdapters/docs/SEVEN_AGENT_BENCHMARK_REPAIR_PLAN.md`。
