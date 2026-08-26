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
- `autoresearch`：冻结 reconstruction protocol、统一模型配置、
  host evaluator、双 held-out gate、smoke/pilot、N=1/N=3 aggregate 和 scorecard CLI；
  Arbor 原版使用官方 plugin evaluator contract；AiScientist 与 ML-Master 原版在该任务
  unsupported。当前尚无 real smoke 或正式横向分数。
- `optimizer-design`：冻结 modded-NanoGPT Track 3 commit、Benchmark 公共 Adapter、四 H100
  资源锁、双 held-out step score 和 N=1/N=3 scorecard；当前双 seed baseline 记录尚未晋级，
  正式入口会直接退出，也不发布正式排名。
- `fml`：first-class pinned-upstream formal Adapter；shared host evaluator、formal evidence
  和双指标 aggregate 已实现。当前 upstream dirty、主指标
  未人工冻结，因此 formal preflight 会直接退出。旧 `FLM-bench/` 路径只保留
  `max_steps=1` 的 non-formal smoke 兼容层。
  详见 `BenchmarkAdapters/docs/FML_SEVEN_AGENT_ADAPTER.md`。

## 七 Agent

| Agent | MLE 原生路径 | Terminal AO 原生路径 | Optimizer Design 小 Adapter |
|---|---|---|---|
| EAR | EAR Docker graph search | EAR KTS/Thompson repository backend | EAR KTS |
| MLEvolve | MLEvolve Docker search/fusion | **不适用**（任务形状不匹配，见下） | MLEvolve UCT |
| Arbor | 原版 unsupported；显式 `arbor-benchmark-patched` | 官方 `arbor run` + plugin evaluator | 官方 `arbor run` + plugin evaluator |
| Codex | `codex exec` public-only workspace | native `codex exec` repository loop | Codex CLI |
| Claude Code | `claude --print` public-only workspace | native `claude --print` repository loop | Claude CLI |
| ML-Master 2.0 | 官方 `run.py --agent ml_master_2` 完整 workflow | **不适用**（任务形状不匹配，见下） | 原版 unsupported；显式 staged variant |
| AiScientist | 官方 `aisci mle run` | 原版 unsupported；显式 terminal variant | 原版 unsupported；显式 architecture variant |

### Terminal AO 的适用边界：5 个 Agent

Terminal AO 的比较集合是 **EAR / Arbor / Codex / Claude Code / AiScientist** 五家，
不是全部七家。唯一权威来源是 `thin_registry.terminal_ao_agents()`；所有 AO 分母、
dispatch 表和 readiness 遍历都必须引用它，不得各自硬编码七家。

MLEvolve 和 ML-Master 2.0 被排除，原因是**任务形状不匹配，而不是 Agent 能力不足**。
两者作为 Kaggle 形态的 ML 工程 Agent 都是完整可用的，在 MLE-Bench Lite 上均原生运行
22 题、不受本次改动影响：

- **MLEvolve**：其搜索引擎用「候选节点是否产出 `submission.csv`」判定成功
  （`baselines/MLEvolve/engine/execution.py:26-30`），并按同一路径管理最优解
  （`baselines/MLEvolve/engine/solution_manager.py:71,169`；
  `baselines/MLEvolve/agents/debug_agent.py:78`）。
- **ML-Master 2.0**：其 playground 写死 Kaggle 形状的 workspace
  （`best_submission`/`best_solution`/`submission`/`working`），并通过复制
  `submission_<uid>.csv` 晋升最优解
  （`baselines/EvoMaster/playground/ml_master_2/core/playground.py:107-113,212,300`）。

而 Harness Engineering AO 的候选是冻结 `terminus-2` 仓库的 git revision、由聚合 dev
pass rate 评分，永远不产生这类 csv 产物。要让它们参与，就必须重写各自引擎的核心判定
逻辑——那样跑出的分数衡量的是我们的改写，而不是该 Agent。

先前 `TerminalAO/launchers/{mlevolve,ml_master_2}.py` 曾用 benchmark 自己写的外层循环、
prompt、diff 提取、评估与选优，包住上游的一个函数或一段 prompt 序列。把那种结果记为该
Agent 的 AO 分数，等于把 harness 的行为归因给 Agent。因此这两个入口改为 fail-closed 存根，
在 `TerminalAOAdapter.__init__`、`build_native_ao_command` 和 launcher 三层各自抛
`UnsupportedAdapterError`，而不是保留一个带脚注的数字。

原版 ID 与显式变体分离：`arbor-benchmark-patched`、
`ai-scientist-terminal-variant`、`ai-scientist-architecture-variant`、
`ml-master-autoresearch-variant` 不进入原版七 Agent registry，也不会被原版 ID 自动 fallback。

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
- Terminal AO scorecard 的 `complete_comparison_set_valid` 以 5 家比较集合为分母，
  并在 `comparison_set` / `excluded_agents` 中显式记录集合与排除理由；
  排除的 Agent 不计入分母，也不会因凑不齐七家而withhold 排名。
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

# 所有正式 run 先执行结构化 preflight；条件不满足时直接退出并记录原因
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
