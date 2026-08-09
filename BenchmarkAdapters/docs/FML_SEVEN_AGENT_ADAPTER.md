# FML-Bench 七 Agent Formal Adapter

## 两层架构

```text
Frozen FML task YAML + upstream task assets
                    |
                    v
        FML shared benchmark layer
  protocol -> canonical task -> workspace
  dev broker -> host evaluator -> evidence
  final artifact -> held-out evaluator -> aggregate
                    |
                    v
          FMLAgentAdapter interface
                    |
    +-------+-------+-------+-------+
    |       |       |       |       |
   EAR  MLEvolve  Arbor   Codex  Claude Code
    |       |       |       |       |
    +-------+--- ML-Master --+-- AiScientist
                    |
                    v
       normalized Agent run result
```

共享层位于 `BenchmarkAdapters/FMLBench/adapter.py`、`task.py`、`workspace.py`、
`broker.py`、`evaluator.py`、`runner.py`、`records.py` 和 `aggregate.py`。共享 runner
只通过 `FMLAgentAdapter` 调用 Agent，不包含七路 Agent 分支。

七个专属 Adapter 位于 `BenchmarkAdapters/FMLBench/agents/`。它们只转换 task/prompt、
CLI/API、模型配置、运行时和轨迹，不生成 proposal、不修复代码、不选择 final candidate、
也不计算正式分数。

## Agent Matrix

| Agent | Adapter class | Native entrypoint | Installation probe | Task / artifact | Fake relay + synthetic E2E | 当前 blocker |
|---|---|---|---|---|---|---|
| EAR | `EARFMLAdapter` | `agent.engine.thompson.select_parent` | Python + native import | canonical task；editable-path tar | 通过 | 正式 variant 仍需选择 `g3` 或 `full` |
| MLEvolve | `MLEvolveFMLAdapter` | `AgentSearch` + UCT selector | Python + native import | canonical task；editable-path tar | 通过 | 无 Adapter blocker |
| Arbor | `ArborFMLAdapter` | `arbor run` coordinator | exact CLI/version/hash | canonical task；editable-path tar | 通过 | 尚未真实 relay smoke |
| Codex | `CodexFMLAdapter` | `codex exec` | exact CLI/version/hash | canonical task；editable-path tar | 通过 | 尚未真实 relay smoke |
| Claude Code | `ClaudeCodeFMLAdapter` | `claude --print` | exact binary/version/hash | canonical task；editable-path tar | 通过 | 尚未真实 relay smoke |
| ML-Master 2.0 | `MLMasterFMLAdapter` | `BaseAgent.run` staged workflow | Python + native import | canonical task；editable-path tar | 通过 | 当前锁定 runtime 的 `mcp` API 不兼容，native import 失败 |
| AiScientist / AweAI | `AiScientistFMLAdapter` | `TerminalTaskSubagent.run` | Python + native import | canonical task；editable-path tar | 通过 | 尚未真实 relay smoke |

AiScientist Adapter 明确接入本项目注册的 AweAI AiScientist，不复用 FML upstream 的
The AI Scientist v1/v2 baseline。

## Formal Evidence

每个 task cell 保存：

- schema-v2 run manifest、protocol/task/model/Agent/Adapter identity；
- canonical task、脱敏 rendered instruction 和 initial workspace digest；
- development proposal/evaluation records 与一次性 held-out evaluation record；
- normalized Agent result、stdout/stderr、trajectory 和 token/request/cost telemetry；
- 仅含 editable paths 的 deterministic tar artifact；
- task record 中所有 evidence 的 SHA-256。

aggregate 不信任 Agent 或旧 upstream `summary.json` 中的最终分数，而是校验 artifact、
Agent result、held-out evaluator record、manifest 和 task record 后，从 task-level held-out
metric 重算 upstream normalized improvement 与 win rate。

## Upstream Protocol Audit

`fml-review-protocol` 从 pinned upstream 客观生成 review candidate，但不自动晋级。当前审计：

- upstream commit：`d336651ebea50c622c256f02ded82b68b4451fdc`；
- task set：完整 18 题；
- evaluator identity：upstream runner/executor/utils/metrics implementation digest；
- score：逐题 baseline fallback/range 和 normalized improvement 公式已映射；win rate 同时输出；
- upstream repository 当前 dirty，因此 formal preflight 必须失败；
- upstream registry 的 baseline Agents 与本项目七 Agent 不同，正式路径使用本项目 concrete Adapter registry；
- 当前未人工决定主榜使用 average improvement 还是 win rate。

仍需人工冻结：

1. `primary_metric_name`：`average_improvement` 或 `win_rate`；
2. review candidate 的 upstream commit/task/evaluator/range 审核；
3. 50 proposal/evaluator-call 和 48h wall-clock policy；
4. EAR 正式 variant；
5. H100/Conda environment identity 与 Agent runtime revisions；
6. clean Adapter commit 后把 `formal_status` 从 `review-required` 晋级为 `frozen`。

## Legacy Isolation

- canonical formal benchmark ID 只有 `fml-bench`，且只由 `BenchmarkAdapters/FMLBench/` 注册；
- `BenchmarkAdapters/FLM-bench/` 保留 `max_steps=1` legacy smoke；
- formal protocol 拒绝 `max_agent_steps <= 1`；
- formal aggregate 只接受 schema-v2 task/evaluation/manifest evidence；
- legacy schema-v1、Codex 单任务 smoke、non-formal/non-comparable records 均不能升级或进入正式 aggregate。

## Commands

```bash
# 生成 review candidate，不自动晋级
python3 -m BenchmarkAdapters fml-review-protocol \
  --upstream-root /mnt/sda/shijianwang/benchmark-deployments/repos/FML-bench \
  --output /tmp/fml-protocol-review.json

# 查看真实 installation/protocol/evidence readiness
python3 -m BenchmarkAdapters fml-readiness \
  --protocol /tmp/fml-protocol-review.json

# deterministic tests；不会调用真实 API/GPU
python3 -m pytest -q BenchmarkAdapters/tests/test_fml_bench.py
```

## H100 Deployment Remaining

1. 恢复并清理 upstream FML worktree；审核、提交并锁定 Adapter 源码。
2. 修复 ML-Master 2 runtime 的 `mcp` 依赖并重跑 native import probe。
3. 验证 18 个 task Conda 环境、dataset 和 evaluator command。
4. 人工选择 primary metric，并审核晋级 protocol candidate。
5. 为七个 Agent 分别完成 non-comparable real launcher smoke，验证 relay、trajectory 和 artifact。
6. 在相同 H100、model track、wall-clock 和 outer repetition 下运行正式 campaign。

当前没有正式 FML 分数，`formal_scored` 必须保持 `false`。
