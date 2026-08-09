# BenchmarkAdapters

`BenchmarkAdapters/` 是七个 Agent 的 canonical 适配层。MLE-Bench Lite、
Terminal-Bench AO、Autoresearch Architecture Design、modded-NanoGPT Optimizer
Design 和 FML-Bench 均已有 formal contract；小写 `benchmark_adapters/` 只做兼容性
re-export。

## 正式模式

- `mle` / `mle-cell`：冻结的 22 题 MLE-Bench Lite；最终 `submission.csv`
  由 host-owned 官方 `mlebench.grade.grade_csv` 评分。
- `terminal-ao`：`terminal-bench-ao-reconstruction-v1`；外层 Agent 在 48 小时内
  只使用 36-task dev broker 优化同一 `terminus-2`，冻结后一次性运行 53-task
  held-out test。
- `terminal-direct-smoke`：89 题直接解题基础设施 smoke，明确
  `non_comparable_to_terminal_ao=true`，不得进入 AO 榜单。
- `autoresearch`：冻结 reconstruction protocol、七个原生 Agent bridge、统一模型配置、
  host evaluator、双 held-out gate、smoke/pilot、N=1/N=3 aggregate 和 scorecard CLI；
  当前尚无七 Agent real smoke 或正式横向分数。
- `optimizer-design`：冻结 modded-NanoGPT Track 3 commit、Benchmark 公共 Adapter、七个
  小 Agent Adapter、四 H100 资源锁、双 held-out step score 和 N=1/N=3 scorecard；当前因
  双 seed baseline 记录尚未晋级而 fail-closed，不发布正式排名。
- `fml`：first-class pinned-upstream formal Adapter；正式 task/round/proposal 语义、任务集、
  evaluator、七个 concrete launcher 和 wall-clock 均来自显式冻结协议。旧 `FLM-bench/`
  路径只保留 `max_steps=1` 的 non-formal smoke 兼容层。

## 七 Agent

| Agent | MLE 原生路径 | Terminal AO 原生路径 | Optimizer Design 小 Adapter |
|---|---|---|---|
| EAR | EAR Docker graph search | EAR KTS/Thompson repository backend | EAR KTS |
| MLEvolve | MLEvolve Docker search/fusion | `AgentSearch`/`SearchNode` UCT repository backend | MLEvolve UCT |
| Arbor | native Arbor MLE runner | native Arbor coordinator | Arbor coordinator |
| Codex | `codex exec` public-only workspace | native `codex exec` repository loop | Codex CLI |
| Claude Code | `claude --print` public-only workspace | native `claude --print` repository loop | Claude CLI |
| ML-Master 2.0 | generated per-run config + native workflow | EvoMaster native Agent repository workflow | EvoMaster workflow |
| AiScientist | native `aisci mle run` | native `TerminalTaskSubagent.run` | AiScientist subagent |

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
- 五个 Benchmark 分别排名；同一 scorecard 只接受相同 model-config digest、硬件指纹和
  Adapter commit。N=1 标记 `single_run` 且标准差为空；只有 N=3 才标记 `avg_at_3`。
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
  --data-root /data/mle-bench --campaign-dir /runs/mle \
  --model-config /secure-config/model-track.json --agent-variant pinned-codex

# 运行一个 Terminal AO seed；正式 timeout 固定为 172800 秒
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters terminal-ao \
  --agent ear --protocol terminal-bench-2/ao_protocol/protocol.json \
  --output-dir /runs/ao/ear/run-0 --seed 0 --gpu-id 0 --gpu-id 1 \
  --gpu-id 2 --gpu-id 3 --gpu-id 4 --gpu-id 5 --gpu-id 6 --gpu-id 7 \
  --model-config /secure-config/model-track.json --agent-variant g3@FULL_COMMIT

# 分别聚合
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters mle-aggregate \
  --protocol /runs/mle/protocol.json --campaign-dir /runs/mle --agent ear
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters terminal-ao-aggregate \
  --protocol terminal-bench-2/ao_protocol/protocol.json \
  --campaign-dir /runs/ao --agent ear

# Autoresearch real smoke；结果明确 non-comparable
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters autoresearch \
  --agent ear --protocol autoresearch/protocol/protocol.json \
  --prepared-root /srv/autoresearch-prepared \
  --kernel-cache-root /srv/flash-attention-3 \
  --environment-python /srv/autoresearch-env/bin/python \
  --output-dir /runs/autoresearch-smoke/ear --seed 0 --gpu-id 0 \
  --cpu-set 0-31 --memory-limit-gib 128 --smoke --outer-budget-seconds 1800 \
  --model-config /secure-config/model-track.json --agent-variant g3@FULL_COMMIT

# Optimizer Design 命令检查；不调用模型或生成分数
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters optimizer-design \
  --agent ear --protocol optimizer-design/protocol/protocol.json \
  --output-dir /tmp/optimizer-design-ear --seed 0 --dry-run \
  --model-config /secure-config/model-track.json --agent-variant g3@FULL_COMMIT

# 所有正式 run 先执行结构化 fail-closed preflight
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters formal-preflight \
  --benchmark autoresearch-architecture --agent ear --agent-variant g3@FULL_COMMIT \
  --protocol autoresearch/protocol/protocol.json \
  --model-config /secure-config/model-track.json --output /runs/preflight.json
```

## Readiness 含义

`source_ready`、`environment_ready`、`command_ready`、`real_smoke_ready`、
`formal_protocol_ready` 是逐层证据，不是同义词。当前 contract/synthetic 测试、七路
dry-run、锁定 Agent runtime import/CLI probe 和外部 benchmark 资产验证只能证明
`command_ready`；
Optimizer Design 还要求先晋级受保护的双 held-out baseline 记录；当前该 gate 为 pending。
只有归档真实 smoke/formal evidence 且 source clean 后，status
才允许显示更高层级，也才可发布正式横向比分。完整修复和验收清单见
`BenchmarkAdapters/docs/SEVEN_AGENT_BENCHMARK_REPAIR_PLAN.md` 和
`BenchmarkAdapters/docs/OPTIMIZER_DESIGN_SEVEN_AGENT_ADAPTER.md`。
