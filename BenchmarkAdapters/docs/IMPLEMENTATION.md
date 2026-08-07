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
credential 或主仓库。Supervisor 在 outer loop 结束后停止 dev broker，按 dev 分选择并
replay allowlisted revision，然后在写入 one-shot gate 后运行一次 53-task test。

这只是保证 36-dev 搜索与 53-test 最终评分不混用的最小公平边界，不包含针对恶意 Agent
的额外防作弊系统、sealed candidate worker 或全局消费账本。

## Scoring

- MLE：每 seed 固定 22 分母；报告 valid、above median、any medal、gold；raw score
  只做同题比较，不跨题平均。
- Terminal AO：每 seed 固定 53 分母；缺失/error 计零；三 seed 报 mean、sample
  standard deviation、SEM 和 95% CI。
- `scorecard` 将两类分数分栏，`composite_score` 固定为 `null`。

## Validation State

仓库测试覆盖 schema 不可覆盖、官方 grader、22-task campaign、七 MLE artifact、36/53
split、Harbor result parser、candidate 隔离、非法 diff/symlink、dev-only capability、
七 native launcher contract、synthetic closeout、one-shot test 和真实 Bubblewrap 隐藏性；
完整 `BenchmarkAdapters/tests` 为 81 passed，最终 relay 公平参数修复后的 adapter/core
定向回归为 35 passed。本轮验证未实际调用收费模型 API。

两次 MLE 和两次 Terminal AO 正式入口已验证会在 dirty source 上输出结构化失败。真实
`formal_protocol_ready` 仍要求干净 commit、API、Docker、GPU、完整预算和归档的真实
scored evidence；当前七 Agent 均只能声明 `command_ready`，不能声明已有正式比分。
