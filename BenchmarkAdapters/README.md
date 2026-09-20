# BenchmarkAdapters

`BenchmarkAdapters/` 是 EAR 与六个 baseline 的统一适配层；小写
`benchmark_adapters/` 仅保留兼容性导入。本页汇总当前能力、计分规则和本机状态。
[启动手册](docs/CAMPAIGN_LAUNCH.md)维护运行步骤，
[Agent 版本记录](docs/ON_DISK_AGENT_VERSIONS.md)维护源码身份，
[文档索引](docs/README.md)链接各 Benchmark 与 Agent 的详细说明。

## 本机状态（2026-09-20 核验）

| 项目 | 已核实的状态 |
|---|---|
| MLE-Bench Lite | 22 题，schema-2 data manifest；本轮 seed `[0]`、每题 12h，已有官方评分记录，完整横评尚未完成 |
| Terminal-Bench AO | `terminal-bench-ao-reconstruction-v1`，schema-2 protocol，36 dev / 53 held-out，seed `[0]`、外层 48h；本次未核实到完整五家正式成绩 |
| 模型 | `configs/model-track.gpt-5.6-terra-host-relay.json`；外层与 AO 内层均为 `gpt-5.6-terra`，temperature 1.0、reasoning effort high |
| 共享 relay | 上述配置指向宿主机 `127.0.0.1:6201`；sandbox 内的 6200 是另一层入口；v7 使用独立 slot 配置，详见启动手册 |
| Adapter 环境 | `BenchmarkAdapters/.venv` 已安装 PyYAML 6.0.3；此前缺依赖的准备阶段已经结束 |
| 源码 | 主实验工作区及六个嵌套 checkout 在核验时干净；具体 commit 与 Arbor 两棵树的区别见版本记录 |
| 实验生命周期 | 9 月 4 日的三个 MLEvolve 容器仍暂停；9 月 20 日五任务补跑的 controller 为 `paused`，部分进程以 SIGSTOP 暂停 |

当前问题是完成和核验剩余实验，不是重新生成已冻结的协议。schema 2、环境已安装或
命令可构造，都不等于完整横向成绩已经产生。暂停状态的依据在本机
`.runtime/campaign-pause-20260917T172424.json`、`.runtime/campaign-pause-20260920.json`
及相应 campaign 的 `controller.json` / `pause.json`；这些运行记录不随 Git 分发。

`analysis/experiment-status-matrix-20260919.md` 是 9 月 19 日快照，不能替代后续
v5/v6/v7 的结果核验。比较时继续按 model-config、硬件、adapter commit 和协议身份分组，
不得拼接不同补跑配置的最好分。早期 gpt-5.5 实验单独保留。

## 模式与比较集合

| Agent | MLE-Bench Lite | Terminal AO |
|---|---|---|
| EAR | 原生 Docker graph search，G3 | 原生 KTS/Thompson repository backend |
| MLEvolve | 原生 Docker search/fusion | 不参与 |
| Arbor | 显式 `arbor-benchmark-patched` | 官方 `arbor run` + plugin evaluator |
| Codex | 原生 CLI + public-only workspace | 原生 CLI repository loop |
| Claude Code | 原生 CLI + public-only workspace | 原生 CLI repository loop |
| ML-Master 2.0 | EvoMaster 的 `run.py --agent ml_master_2` | 不参与 |
| AiScientist | `aisci mle run` | 显式 `ai-scientist-terminal-variant` |

MLE 共七家；AO 共五家，以 `thin_registry.terminal_ao_agents()` 为准。
MLEvolve 和 ML-Master 的候选成功/晋升依赖 Kaggle 形状的 `submission.csv`，无法直接
表示 AO 的 harness revision。其 AO 入口在 adapter、dispatch 和 launcher 层明确拒绝，
不为凑齐七家而改写上游搜索算法。具体证据见
[MLEvolve AO](docs/adapters/terminal-bench-ao.mlevolve.md)与
[ML-Master AO](docs/adapters/terminal-bench-ao.ml-master-2.md)。

`terminal-direct-smoke` 直接解 89 题，只验证基础设施，标记
`non_comparable_to_terminal_ao=true`。其分数不进入 AO 榜单。

AutoResearch、Optimizer Design 和 FML 的代码/协议也保留在统一 CLI 中，但不属于这台
4090 主机正在进行的双 Benchmark 横评。它们各自的 source、data、runtime、baseline
和真实评测证据仍按专门文档验收；不能由 MLE 的运行状态推断其就绪，也不能直接删掉
被 CLI 导入的模块。详见[其他 Benchmark 文档](docs/README.md)。

## 实现与公平边界

- `protocol.py`、`records.py`、`artifacts.py`、`readiness.py`：协议、不可覆盖的
  manifest/result、产物 hash 和分层证据。
- `MLEBenchLite/`：22 题 membership、七家 launcher、host-owned 官方 grader、
  data manifest、campaign、聚合与运行监控。
- `TerminalAO/`：36/53 split、terminus-2 baseline、Harbor evaluator、revision store、
  dev broker、五家原生 launcher 和一次性 held-out supervisor。
- `TerminalBench/`：直接解题 smoke；`terminal-bench-2/agent_adapters/shared/harbor_shell.py`
  是 Harbor 环境桥接，不替代 AO 协议。
- `LLMRelay/`：共享模型配置、协议适配、凭据隔离和 token 记账；行为定义见
  [relay 说明](LLMRelay/README.md)。
- `AutoResearch/`、`OptimizerDesign/`、`FMLBench/`：其他 Benchmark 的正式适配层；
  `FLM-bench/` 仅保留旧的非正式 smoke 兼容路径。

MLE Agent 只见 prepared public 数据，private label 与官方 grader 留在 host。
AO 外层只得到可写 candidate、自己的锁定 runtime、`evaluate-dev` Unix capability
和 host relay socket；不会挂载 split、89 题 dataset、held-out ID 或 host 凭据。
Dev evaluation 在 disposable copy 中执行，只接受冻结 allowlist 内的 revision。
结束时关闭 dev broker，冻结最终候选，记录 one-shot gate 后评测 53 个 held-out task。
这些是实验公平边界，不是对恶意 Agent 的额外防作弊系统。

## 最终候选由 Agent 决定

最终提交属于 Agent 的能力。Terminal AO、AutoResearch 和 Optimizer Design 都采用
`agent-declared` 选择：

1. Agent 通过 declaration 声明自己的候选；AR/OD 使用 `declare-final`，AO 使用
   `CandidateDevBroker.declare_current()`。
2. 未显式声明时，以运行结束时 workspace 留下的状态作为提交，由 host 原样评估和声明。
3. 没有可声明产物则记失败，保留在分母中；host 不从历史 dev 候选里代选最优解。

`selection.json` 记录 `selection_policy_id`、
`harness_selected_among_candidates: false`、`selection_uses_test/held_out: false`。
聚合器回放这些字段，并输出 `selection_policy_by_agent` 与
`uniform_selection_policy_valid`。`broker.best` 仅用于搜索反馈。

## 计分与证据

- MLE 每个 seed 固定 22 题分母，报告 valid、above median、any medal、gold；
  `submission.csv` 由官方 `mlebench.grade.grade_csv` 评分。不同题目的 raw score 不求平均。
- AO 每个 seed 固定 53 个 held-out task；缺失、错误和超时按协议计零，同时报告基础设施错误。
  比较集合固定为五家，记录 `comparison_set` 与 `excluded_agents`。
- 本机冻结协议为 **N=1**，标记 `single_run`，仅给 mean，标准差、SEM、95% CI 为 `null`。
  N=3 才报告 Avg@3；使用 Student-t，df=n−1，n=3 时临界值 4.3027。不能把 N=1 次序写成显著差异。
- 五个 Benchmark 分别排名；MLE 与 AO 分栏输出，`composite_score` 为 `null`。
- `source_ready`、`environment_ready`、`command_ready`、`real_smoke_ready`、
  `formal_protocol_ready` 是不同层次；contract/synthetic 测试不能代替真实评分证据。

历史设计、修复编号和离线验证记录保留在
[修复与验收规范](docs/SEVEN_AGENT_BENCHMARK_REPAIR_PLAN.md)。其中早期 N=3 计划、
旧 protocol digest 和历史测试数量不是今天的运行状态。

## 操作入口

新建 cell、formal-preflight、失败重试、聚合和当前暂停批次的处理均见
[启动手册](docs/CAMPAIGN_LAUNCH.md)。该手册集中维护命令和端口，其他文档不再复制一套。
CLI 的 `codex-budget-loop` / `claude-code-budget-loop` 是显式续跑变体，必须按 manifest
与原版单次会话区分；不得将变体成绩自动归入原版。逐 Agent 的接入差异见[适配文档索引](docs/adapters/README.md)，环境安装见
[环境矩阵](environments/README.md)。
