# MLEvolve × Terminal-Bench AO — 不参与

| | |
|---|---|
| 状态 | **排除在 AO 比较集合之外** |
| registry `terminal_ao_backend` | `unsupported:Kaggle-shaped search engine; candidate success is decided by submission.csv production` |
| launcher | `TerminalAO/launchers/mlevolve.py` — fail-closed 存根 |

## 为什么排除

MLEvolve 的搜索引擎用「候选节点是否产出 `submission.csv`」判定成功：

- `baselines/MLEvolve/engine/execution.py:26-30`
- `baselines/MLEvolve/engine/solution_manager.py:71,169`
- `baselines/MLEvolve/agents/debug_agent.py:78`

AO 的候选是冻结 `terminus-2` 仓库的 git revision，由聚合 dev pass rate 评分，
**永远不产生这类 csv 产物**。要让 MLEvolve 参与，就得重写它引擎的核心判定逻辑——
那样跑出的分衡量的是我们的改写，不是 MLEvolve。

**这是任务形状不匹配，不是 Agent 能力不足。** MLEvolve 在 MLE-Bench Lite 上
原生跑满 22 题，完全不受影响，见
[mle-bench-lite.mlevolve.md](mle-bench-lite.mlevolve.md)。

## 之前是什么样

这个 launcher 曾经有实现：一个 benchmark 自己写的外层循环、prompt、diff 提取、
评估与选优，包住 MLEvolve 的单个 selector
（`engine.node_selection.select_with_soft_switch`）。

把那种输出记成 MLEvolve 的 AO 分数，等于把 harness 的行为归因给 Agent。
所以这一格是**删掉，而不是留一个带脚注的数字**。

## 现在的 fail-closed 三层

| 层 | 行为 |
|---|---|
| `TerminalAOAdapter.__init__` | agent 不在 `terminal_ao_agents()` 里 → `UnsupportedAdapterError` |
| `build_native_ao_command` | 同上，第二道 |
| `launchers/mlevolve.py::run_native_loop` / `main` | 第三道 |

在任何 protocol / workspace / model 准备之前就拒绝，被排除的 Agent 永远无法
产出一个 Terminal AO 产物。

## 对分母的影响

AO 的比较集合是 **5 家**（EAR / Arbor / Codex / Claude Code / AiScientist），
不是 7 家。唯一权威来源是 `thin_registry.terminal_ao_agents()`；
所有 AO 分母、dispatch 表、readiness 遍历都必须引用它，不得各自硬编码 7。
