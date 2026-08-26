# Benchmark Adapter Implementation

## Canonical Structure

- `BenchmarkAdapters/protocol.py`、`records.py`、`artifacts.py`、`readiness.py`：
  不可变协议、manifest/result、hash-bound artifact 和分层证据。
- `BenchmarkAdapters/MLEBenchLite/`：22-task membership、七 Agent launcher、host
  official grader、source-archive data manifest、campaign 和跨 seed 聚合。
- `BenchmarkAdapters/TerminalAO/`：冻结 36/53 split、terminus-2 baseline、Harbor
  evaluator、revision store、dev-only broker、七 native launcher、Bubblewrap 和 one-shot
  sealed test supervisor。
- `BenchmarkAdapters/TerminalBench/`：仅保留 89 题直接解题 smoke，不属于 AO 分数。

## Terminal AO Fairness Boundary

正式 outer Agent 进程只得到：

1. 从同一 baseline materialize 的 writable candidate；
2. 当前 Agent 的 locked runtime；
3. 只支持 `evaluate-dev` 的 Unix socket capability；
4. host-owned LLM relay Unix socket。

它不会得到 protocol/split 文件、89-task dataset、test ID、test evaluator、Harbor
credential 或主仓库。Supervisor 在 outer loop 结束后停止 dev broker，replay **Agent 自己
声明的** allowlisted revision，然后在写入 one-shot gate 后运行一次 53-task test。

这只是保证 36-dev 搜索与 53-test 最终评分不混用的最小公平边界，不包含针对恶意 Agent
的额外防作弊系统、sealed candidate worker 或全局消费账本。

## Final Artifact Selection Policy

「最终交哪个候选」是 Agent 能力的一部分，因此 Terminal AO、Autoresearch Architecture
Design 和 Optimizer Design 三个 benchmark 对**每一个** Agent 使用同一条规则：

- Agent 自选。harness 不再从所有 dev 评估过的候选里按 dev 分代选最好的
  （`broker.best` 不再参与最终选择，只保留给搜索期反馈用）。
- 统一接口是 declaration：AR/OD 走 broker 的 `declare-final` 操作
  （`dev_client.declare_current`），AO 走 `CandidateDevBroker.declare_current()`。
- 没有显式声明的 Agent（CLI 型等），run 结束时 workspace 里留下的当前状态即为其提交，
  由 host 原样评估并声明；host 绝不改选别的候选。
- Fail-closed：完全没有可声明产物（崩溃、超时、workspace 为空）的 run 记为失败，
  保留在分母里，不回退到 harness 代选。
- 每个 run 在 `selection.json` 记 `selection_policy_id`（当前恒为 `agent-declared`）、
  `harness_selected_among_candidates: false` 和 `selection_uses_test/held_out: false`；
  三个 aggregate 会 replay 并校验这些字段，并在 scorecard 上给出
  `selection_policy_by_agent` 与 `uniform_selection_policy_valid` 两列，
  这样将来若某个 Agent 走了不同路径可以被看见而不是静默。

## Scoring

- MLE：每 seed 固定 22 分母；报告 valid、above median、any medal、gold；raw score
  只做同题比较，不跨题平均。
- Terminal AO：每 seed 固定 53 分母；缺失/error 计零；三 seed 报 mean、sample
  standard deviation、SEM 和 95% CI。
- 95% CI 用 Student-t 临界值（`formal_contract.student_t_two_sided_critical_value`），
  自由度按实际 `outer_repetitions - 1` 计算，不是硬编码的正态分位数。n=3 时
  t(0.975, df=2)=4.3027；旧代码用 z=1.96，把区间宽度低估约 2.2 倍。summary 里
  额外记录 `ci95_method`、`ci95_degrees_of_freedom`、`ci95_critical_value` 以便审计。
- `scorecard` 将两类分数分栏，`composite_score` 固定为 `null`。

## Validation State

仓库测试覆盖 schema 不可覆盖、官方 grader、22-task campaign、七 MLE artifact、36/53
split、Harbor result parser、candidate 隔离、非法 diff/symlink、dev-only capability、
七 native launcher contract、synthetic closeout、one-shot test 和真实 Bubblewrap 隐藏性；
完整 `BenchmarkAdapters/tests` 为 81 passed，最终 relay 公平参数修复后的 adapter/core
定向回归为 35 passed。本轮验证未实际调用收费模型 API。

两次 MLE 和两次 Terminal AO 正式入口已验证会在 dirty source 上输出结构化失败。真实
正式运行还需要干净 commit、API、Docker、GPU、完整预算和归档的真实 scored evidence。
当前状态不是“七个 Agent 都已经可以评分”：

| 项目 | 当前状态 |
|---|---|
| Adapter 类、Agent launcher、artifact 和 scorecard 代码 | 已写入，可做 contract/synthetic 检查 |
| MLE-Bench Lite 正式资产 | `data_manifest.json` 仍是 schema 1，缺 prepared public/private hashes |
| Terminal AO 正式协议 | 当前 `protocol.json` 仍是 schema 1，缺 benchmark source commit |
| 模型配置 | 仍是 placeholder，不能用于正式运行 |
| Adapter Python 环境 | `BenchmarkAdapters/.venv` 缺 PyYAML，当前不能直接导入顶层包 |
| Agent 源码 | EAR、MLEvolve、Arbor、ML-Master 2.0、AiScientist 当前有未提交变化 |
| 真实 scored smoke 和完整 campaign | 尚未完成 |

因此，当前最多能说明“部分命令可以构造”，不能说明已经产生正式横向分数。
