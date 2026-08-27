# Agent × Benchmark Adapter 文档

每个 Agent 在每个 benchmark 上「怎么接」的逐格说明。一格一篇，命名
`<benchmark-id>.<agent-id>.md`。

这一层回答的是 **adapter 做法**：入口在哪、谁拥有搜索循环、Agent 看得见什么、
产物怎么判定、以及这一格特有的偏离。不重复 protocol/评分/门禁的定义——那些在
`../SEVEN_AGENT_BENCHMARK_REPAIR_PLAN.md` 和 `../IMPLEMENTATION.md`。

## 索引

| Agent | MLE-Bench Lite | Terminal-Bench AO |
|---|---|---|
| EAR | [mle-bench-lite.ear.md](mle-bench-lite.ear.md) | [terminal-bench-ao.ear.md](terminal-bench-ao.ear.md) |
| MLEvolve | [mle-bench-lite.mlevolve.md](mle-bench-lite.mlevolve.md) | [terminal-bench-ao.mlevolve.md](terminal-bench-ao.mlevolve.md)（不参与） |
| Arbor | [mle-bench-lite.arbor.md](mle-bench-lite.arbor.md) | [terminal-bench-ao.arbor.md](terminal-bench-ao.arbor.md) |
| Codex | [mle-bench-lite.codex.md](mle-bench-lite.codex.md) | [terminal-bench-ao.codex.md](terminal-bench-ao.codex.md) |
| Claude Code | [mle-bench-lite.claude-code.md](mle-bench-lite.claude-code.md) | [terminal-bench-ao.claude-code.md](terminal-bench-ao.claude-code.md) |
| ML-Master 2.0 | [mle-bench-lite.ml-master-2.md](mle-bench-lite.ml-master-2.md) | [terminal-bench-ao.ml-master-2.md](terminal-bench-ao.ml-master-2.md)（不参与） |
| AiScientist | [mle-bench-lite.ai-scientist.md](mle-bench-lite.ai-scientist.md) | [terminal-bench-ao.ai-scientist.md](terminal-bench-ao.ai-scientist.md) |

MLE 是 7 格，AO 是 5 格（MLEvolve / ML-Master 2.0 因任务形状不匹配被排除，
两篇仍然写出来，说明为什么排除而不是留一个带脚注的数字）。

## 四类 adapter 形态

读某一格之前先知道它属于哪一类：

| 形态 | 谁拥有搜索循环 | 用在哪些格 |
|---|---|---|
| **原生 Docker 启动器** | Agent 自己 | MLE：EAR、MLEvolve、Arbor |
| **原生 host 启动器（bwrap）** | Agent 自己 | MLE：AiScientist、ML-Master 2.0 |
| **通用 workspace + CLI** | Agent 自己（CLI 单次会话） | MLE：Codex、Claude Code |
| **AO native launcher** | Agent 自己 | AO：全部 5 格 |

**没有任何一格由 benchmark 提供搜索循环。** 这是硬约束：adapter 只负责
Agent 不该知道的东西（数据在哪、评估怎么调、产物放哪），一旦 adapter 自己写了
外层循环、prompt 序列、diff 提取或选优规则，跑出来的分衡量的是 harness 而不是
Agent。`TerminalAO/launchers/{mlevolve,ml_master_2}.py` 之前就是这样，现在是
fail-closed 存根。

## 共同约束

- **模型统一**：所有格经 host-owned LLM relay（`http://127.0.0.1:6200/v1`），
  由 `configs/model-track.*.json` 定义 model id / 参数 / 超时 / 重试。
  Agent 拿到的是 relay 端点，不是真 key。
- **来源钉死**：正式格要求 `install_path` 的 nested git 干净，HEAD 那 40 位写进
  manifest。身份见 `../ON_DISK_AGENT_VERSIONS.md`。
- **产物判定**：MLE 认 Agent 自己声明的最终 `submission.csv`（非空、与
  sample_submission 不同、比运行前新）；AO 认 Agent 自己留在 trunk 上的
  revision。都不是 host 替 Agent 挑最好的那个。
- **私有数据留在 host**：MLE 的 private label 与官方 grader 从不进 Agent 容器；
  AO 的 53-task test 在 outer loop 结束、dev broker 关闭之后才跑一次。
