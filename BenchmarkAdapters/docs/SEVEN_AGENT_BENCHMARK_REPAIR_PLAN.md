# 七 Agent 双 Benchmark 修复与正式横向评测计划

## 0. 文档状态

- 文档性质：修复清单、已实现状态和正式验收规范的单一权威文档。
- 实现状态：MLE 评分/campaign、Terminal AO supervisor、七个原生 launcher、统计和 CLI 代码已经写入；完整 `BenchmarkAdapters/tests` 曾通过 81 项，另有 35 项定向回归记录。
- 当前状态：这只能说明代码结构和部分命令检查已经完成。MLE manifest、Terminal AO 正式 protocol、模型配置、adapter Python 依赖和 Agent clean checkout 还没有全部准备好；尚未完成 22×7×3 MLE 与 7×3×48h AO 正式长跑，因此当前没有正式横向榜单。
- 目标 Agent：EAR、MLEvolve、Arbor、Codex、Claude Code、ML-Master 2.0、AiScientist。
- 目标 Benchmark：完整 22 题 MLE-Bench Lite，以及 Terminal-Bench 2.0 的 36 dev / 53 held-out test Harness Engineering AO 协议。
- 明确排除：89 题由七个外层 Agent 直接逐题解题的分数不属于本文档定义的 Terminal-Bench AO 正式分。
- 设计边界：只实现横向比较所需的公平性、数据边界和结果审计；不把恶意 Agent、主动作弊检测、全局消费账本或额外防攻击机制纳入本轮设计。

## 1. 先纠正当前状态的表述

EAR 和 MLEvolve 已经实际运行过 MLE-Bench Lite，产生过 submission，并有官方 grader 结果。本文档不会把它们描述为“不兼容 MLE-Bench Lite”。当前问题是新的统一 Adapter 与历史可用运行路径之间出现了版本和启动回归：

- EAR 历史运行路径可用，但当前 registry 默认 checkout 与 `docker-eval/run_in_docker.sh` 的新 CLI 参数不匹配。
- MLEvolve 历史运行路径可用，但当前统一 launcher 的 format server Python 环境和超时后 fusion 收尾存在回归。
- 这些问题应按 Adapter regression 修复，不能通过重写 EAR 或 MLEvolve 算法来掩盖。

Terminal-Bench 的正式 AO 路径会在协议、模型、源码或产物条件不满足时直接退出，避免生成看似有效的分数。现在 EAR、MLEvolve、ML-Master 2.0 已有独立 AO repository backend；原来的 89 题直接解题入口仍只是 `terminal-direct-smoke`，不会进入 AO 排名。

## 0.1 已实现修复摘要

| 范围 | 当前实现 | 状态 |
|---|---|---|
| Canonical package | `BenchmarkAdapters/` 为唯一实现，`benchmark_adapters/` 仅 re-export | 已完成 |
| 正式 mode | `mle`、`terminal-ao`、`terminal-direct-smoke` 明确分离 | 已完成 |
| MLE membership/grader 代码 | 22 题 membership、host-owned 官方 grader、不可覆盖报告 | 代码已完成；正式 manifest 仍需 schema 2 |
| MLE campaign 代码 | 7 Agent × 22 task × ≥3 seed 网格、失败保留分母、逐题 raw score | 代码已完成；尚无正式 campaign 结果 |
| MLE 原生产物 | 七个 launcher 都声明唯一 `submission.csv`；AiScientist/ML-Master 2 用确定性 wrapper | 已完成 |
| 36/53 AO 资产 | `terminal-bench-ao-reconstruction-v1`、36 dev/53 test、Harbor 0.20、terminus-2 baseline digest | dataset/split 可验证；schema-v2 protocol 仍需生成 |
| AO evaluator | Harbor trial `result.json` 结构化解析；缺失/error 计零；固定分母 | 代码和 synthetic 检查已完成 |
| AO 公平边界 | dev-only Unix capability、disposable evaluator copy、allowlist revision、one-shot final test | 已完成 |
| 七个 AO launcher | EAR KTS、MLEvolve UCT、Arbor coordinator、Codex、Claude、EvoMaster repository workflow、AiScientist Subagent | 已完成 contract/synthetic 验证；真实 smoke 待做 |
| 正式公平隔离 | Bubblewrap 只挂载 candidate、launcher runtime、dev socket 和 host relay socket；不挂载 split/dataset/test | 已完成 |
| 统计/CLI | MLE 与 AO 分栏聚合、Avg@3/CI、status/preflight/scorecard；不生成混合总分 | 代码已完成；依赖和配置未齐 |
| Adapter Python 环境 | `BenchmarkAdapters/.venv` 提供正式 CLI/runtime | 当前缺 PyYAML，需补依赖 |
| Agent 源码版本 | 所有正式 run 使用固定 clean commit | 5 个 Agent 当前有未提交变化 |
| 真实正式长跑 | 需要干净 commit、API、Docker、GPU 和完整时间预算 | 尚未执行 |

## 2. 唯一正式评测口径

### 2.1 MLE-Bench Lite

七个 Agent 都直接完成同一组 22 个 Lite Kaggle 任务。每题读取相同 description 和 prepared public data，最终发布一个 `submission.csv`，由同一版本的官方 `mlebench grade_csv` 评分。

正式结果必须包含：

1. 22 题逐题官方 raw score 和优化方向。
2. `valid_submission`、`above_median`、金银铜牌和 `any_medal`。
3. 22 题上的有效提交率、过中位数率、任意奖牌率和金牌率。
4. 至少 3 个预注册 seed 的均值、标准差和置信区间。
5. wall-clock、LLM token、API cost、GPU/CPU 时间和失败原因。

不同题目的 raw score 指标和方向不同，禁止直接把 22 个 raw score 做算术平均。

### 2.2 Terminal-Bench 36/53 AO

Terminal-Bench 正式任务是外层研究 Agent 优化固定的 `terminus-2` harness：

```text
外层 Agent：EAR / MLEvolve / Arbor / Codex / Claude Code / ML-Master 2 / AiScientist
                                  │
                                  │ 48 小时，只能访问 dev evaluator
                                  ▼
                   修改同一份冻结的 terminus-2
                                  │
                                  │ 内层模型固定为 GPT-5.5
                                  ▼
               36 dev tasks 搜索反馈 + 53 held-out test 最终评分
```

正式协议：

1. 89 道任务冻结为互斥的 36 dev + 53 held-out test。
2. 外层 Agent 只能修改声明的 `terminus-2` Agent、prompt、parser 和 tmux/session 文件。
3. Harbor、任务、verifier、split、API 配置和 baseline record 全部只读。
4. 搜索期间外层 Agent只能调用 dev evaluator，不能知道 test task ID、test 日志或 test reward。
5. 外层 wall-clock 固定为 48 小时，dev evaluator concurrency 固定为 8。
6. 搜索结束后冻结最终 harness；每个独立外层 run 只允许执行一次 53-task test evaluation。
7. 主指标是 held-out test pass rate。dev pass rate 只用于搜索过程和诊断，不是最终比分。
8. 每个 Agent 至少运行 3 个预注册外层 seed，报告 Test Pass Rate Avg@3、标准差和置信区间。

### 2.3 36/53 split 的身份

仓库当前没有论文原始 `data/dev.json`、`data/test.json` 和 paper-time `run_eval.py`。实现时必须二选一，不能模糊表述：

- **严格复现轨道**：从作者或可验证发布物获得原始 split、evaluator 和 paper-time commit，记录原始 SHA-256。
- **本地冻结重建轨道**：按当前 89 题 `task.toml` 的 difficulty 和 category 做确定性分层抽样，生成一次 36/53 split，人工审计后永久冻结 task ID、生成算法、seed 和 SHA-256。该结果必须标记为 `terminal-bench-ao-reconstruction-v1`，不得声称是论文原始 split。

在严格复现资产尚未取得时，项目可以先完成本地冻结重建轨道，使七 Agent 能运行并在**本项目内部**有效横向比较。

## 3. Definition of Done

只有同时满足以下条件，才能宣布“七 Agent 可以正常运行并给出有效横向比分”：

### 3.1 单个 Agent × Benchmark 的完成条件

- 使用冻结的 Agent commit，不从 dirty working tree 启动正式实验。
- preflight 验证 source、环境、数据、模型、预算、镜像和 evaluator digest。
- 真实启动 Agent 的原生控制循环，不用通用 prompt profile 冒充该 Agent。
- 运行时只能看到协议允许的数据和文件。
- 进程失败、超时或缺失 artifact 时直接记为失败，并产生结构化失败记录。
- MLE 产物必须通过官方 grader；Terminal AO 产物必须通过独立 final freeze 和 held-out evaluator。
- 输出统一 result schema 和完整 provenance。
- 至少有一个低成本真实端到端 smoke，不能只靠 import、dry-run 或 fake environment 单测判 ready。

### 3.2 七 Agent 横向比较的完成条件

- 同一任务集合与 digest。
- 同一 Benchmark、grader、Harbor 和 evaluator 版本。
- 主公平轨道使用同一外层模型、reasoning effort、temperature、API endpoint 行为和 retry policy。
- 同一 wall-clock、硬件、CPU/RAM/GPU 配额和并发设置。
- 同一 seed schedule、运行次数、失败计零和 retry 规则。
- 同一最终 artifact 选择和冻结规则。
- 不混用不同 commit、不同代际或不同 seed 的逐题最好分来拼接一行结果。

## 4. 修复后的运行矩阵

| Agent | MLE-Bench Lite 正式路径 | Terminal 36/53 AO 正式路径 | 当前 readiness 上限 |
|---|---|---|---|
| EAR | G3 legacy CLI、显式 artifact、官方 grader | 原生 G3 `thompson.select_parent` KTS repository backend | 命令可构造；当前 source/config/资产未达到正式运行条件 |
| MLEvolve | control-plane format server、显式 artifact、官方 grader | 原生 `AgentSearch`/`SearchNode` + UCT repository backend | 命令可构造；当前 source/config/资产未达到正式运行条件 |
| Arbor | 原版不支持，需 `arbor-benchmark-patched` | 原版要求 clean upstream；patched variant 可用 | 默认入口不能正式用；variant 和 clean source 待确认 |
| Codex | public-only Bubblewrap workspace + host relay/grader | 原生 `codex exec` + AO Bubblewrap/dev capability | 命令可构造；当前 config/资产/adapter 环境未完成 |
| Claude Code | public-only Bubblewrap workspace + host relay/grader | 原生 `claude --print` + AO Bubblewrap/dev capability | 命令可构造；当前 config/资产/adapter 环境未完成 |
| ML-Master 2.0 | clean 原版 `run.py --agent ml_master_2` | 必须使用 `ml-master-autoresearch-variant` | 默认 AO 入口不支持；MLE source 当前有未提交变化 |
| AiScientist | clean 原版 `aisci mle run` | 必须使用 `ai-scientist-terminal-variant` | 默认 AO 入口不支持；MLE source 当前有未提交变化 |

## 5. 共享结构修复

### CORE-001：消除双 Adapter 实现

**问题**：仓库同时存在 `BenchmarkAdapters/` 和 `benchmark_adapters/`，两边包含不同实现，容易运行错误代码。

**修复**：

- `BenchmarkAdapters/` 保持唯一 canonical implementation。
- `benchmark_adapters/` 只保留兼容性 re-export 和 `__main__` 转发；删除其中独立业务逻辑。
- 在测试中断言大小写两个模块解析到同一 registry、request 和 adapter class。
- README 和所有 Agent 文档统一使用 `uv run --project BenchmarkAdapters python -m BenchmarkAdapters ...`。

**修改级别**：结构清理，但不改 Agent 算法。

### CORE-002：拆分 Benchmark mode

**问题**：当前 `terminal` 子命令代表 89 题直接解题，和正式 AO 定义冲突。

**修复**：

- canonical CLI 使用明确命名：`mle`、`terminal-ao`。
- 如保留直接解题工具，必须改名为 `terminal-direct-smoke`，输出中写入 `non_comparable_to_terminal_ao=true`。
- registry 分开声明 `mle_backend`、`terminal_ao_backend` 和可选 `terminal_direct_smoke_backend`，禁止共用一个模糊 `terminal_supported`。

**修改级别**：必要重构。

### CORE-003：统一正式运行 manifest

新增不可变 `RunManifest`，至少记录：

- schema/protocol ID；
- Benchmark、task/split digest；
- Agent key、源 commit、branch、dirty 状态和 launcher backend；
- Adapter、grader/evaluator、relay、lockfile 和容器镜像 digest；
- 模型、provider、endpoint identity、reasoning effort、temperature；
- seed、wall-clock、step/turn/candidate 预算；
- GPU UUID、CPU set、RAM、concurrency；
- retry、failure、artifact selection 和 aggregation policy；
- 开始/结束时间与最终状态。

正式模式拒绝 dirty source、缺 digest、同 run ID 覆盖和不完整 manifest。

### CORE-004：统一 result schema

新增 `BenchmarkRunResult`，公共字段包括：

- `status`: `completed`、`failed`、`timed_out`、`invalid_artifact`、`infrastructure_error`；
- `score_valid` 与 invalid reason；
- Agent/Benchmark/protocol/run/seed identity；
- artifact 路径、SHA-256 和生成时间；
- wall-clock、token、cost 和 compute；
- exception、retry 和 degraded 状态；
- manifest SHA-256。

MLE 扩展保存官方 competition report；Terminal AO 扩展保存 dev history、frozen harness commit/hash、一次性 test report 和 pass rate。

### CORE-005：统一 preflight 和 readiness gate

`BenchmarkAdapters.status` 当前只证明“安装/导入”，不能证明真实可运行。新增分层状态：

1. `source_ready`
2. `environment_ready`
3. `command_ready`
4. `real_smoke_ready`
5. `formal_protocol_ready`

只有第五层可以进入正式 campaign。每个状态必须带证据路径和时间，不允许只由硬编码布尔值决定。

## 6. MLE-Bench Lite 修复清单

### MLE-001：锁定完整 22-task Lite membership

**问题**：当前 adapter 只检查目录存在，不验证 competition 是否属于官方 Lite split。

**修复**：

- 从冻结的 MLE-Bench registry 读取 22 个 Lite competition ID。
- campaign 启动前验证任务集合完全相等，不允许 missing、extra 或重复。
- 记录官方 Lite split digest、MLE-Bench source commit、22 个源 archive 的 size/SHA-256、grader worker 和 lockfile digest；正式运行校验当前任务 archive size，完整 SHA 审计可离线执行。

**修改级别**：最小修复。

### MLE-002：把官方 grader 纳入 canonical pipeline

**问题**：当前 CLI 只返回 submission 路径；“新建且不等于 sample”不等于官方有效。

**修复**：

- Agent 完成后由 host-owned grader 调用冻结版本的 `grade_csv`。
- grader 不进入 Agent namespace，不在搜索期间暴露 private labels。
- `valid_submission=false`、`score=None` 或 grader 异常必须让 `score_valid=false`；不能只打印后以 exit 0 表示成功。
- 保存完整 JSON report，不只打印文本。
- 修复 `docker-eval/grade.py` 的硬编码 data root、所有题都显示 `<=` 的方向错误，以及无效提交仍成功退出的问题；长期应由新 grading service 取代该脚本。

**修改级别**：小到中重构。

### MLE-003：统一 artifact publication

**问题**：不同 Agent submission 路径不同，当前递归找到第一个新 CSV，可能命中无关文件；相同内容的合法新 run 又可能被判 stale。

**修复**：

- 每个 launcher 显式返回 candidate artifact path，不再全树盲搜。
- 先复制到 run-owned staging，验证 regular file、competition format 和官方 grader，再原子发布为 `artifacts/final/submission.csv`。
- artifact identity 使用 run ID + source path + hash + publication event，不以“必须和旧 hash 不同”判断新鲜性。
- sample submission 及其 CSV 语义等价内容继续禁止作为正式产物。

### MLE-004：统一 campaign、seed 和聚合

新增 `mle campaign`：

- 固定 22 tasks × 7 Agents × 至少 3 seeds。
- 每个 cell 独立 output、run ID、relay token log 和 grader report。
- 预注册失败重试规则；非允许的失败按无有效 submission 计入分母。
- 聚合只使用官方 report，输出 valid、above-median、medal 和 gold rates，以及逐题 raw score。
- 禁止 best-of-seeds 作为主结果；可以附加报告，但必须明确标记。

### MLE-005：统一公平预算

- 主轨道固定同一模型 `gpt-5.5`、reasoning effort、temperature、wall-clock、单 GPU、CPU/RAM 和 retry policy。
- `steps`、`max_turns`、`max_cycles` 仅作为 Agent 内部安全上限，主公平预算以 wall-clock 为准。
- Agent 若提前耗尽内部上限，按正常提前停止记录；不能给其他 Agent 补时。
- Token/cost 是效率指标，不替代 wall-clock 主预算。

### MLE-006：EAR Adapter regression

**最小修复方案**：

- registry 不再默认指向旧 `proxy-based-eval` checkout；通过冻结 protocol config 显式指定正式 EAR worktree/commit。
- launcher 在启动前读取 `agent/run.py --help` 或版本化 CLI capability，按 EAR 代际选择参数，不再用隐式 `current` 猜测。
- 正式 run 必须记录 EAR generation、reward mode、source commit 和 CLI schema version。
- 保留历史 `g3_legacy` 复现模式，但与当前代际主结果分行。

**禁止**：为了适配 launcher 修改 EAR 的 Thompson/search 行为。

### MLE-007：MLEvolve Adapter regression

**最小修复方案**：

- format server 使用包含 Flask、OmegaConf 和 `engine.validation` 的 MLEvolve control-plane Python，不再使用 MLE-Bench Lite venv。
- 外层 timeout 必须给 graceful closeout 和 fusion 预留缓冲；Agent 搜索预算与 supervisor hard kill 分开。
- `timeout` 返回 124 时先终止搜索子进程，再在限定 closeout 窗口执行 journal 保存、fusion、artifact validation；closeout 失败则明确标 invalid。
- search/fusion 全部限制在单次 run-owned `MLE_SESSION_ROOT` 中，launcher 只从该隔离目录选择 fusion 结果并显式发布到本 run 的 `submission.csv`，不再扫描共享历史 runs。
- 保持 `coldstart=False` 等当前公平协议决策，并写入 manifest。

### MLE-008：Arbor

- 保留 native `arbor.mle.run` 和 host-owned format service。
- 接入统一 manifest、artifact publication、官方 grader 和 22-task campaign。
- 把 Arbor source commit、metric direction inference、max cycles 和内部 time budget写入结果。
- 新增真实单题 scored smoke，验证 sample fallback 不能被发布。

### MLE-009：Codex

- 保留 Bubblewrap public-only workspace 和 host relay。
- 显式锁定 Codex CLI 版本，不用 PATH 上不受控的 latest。
- 接入统一 artifact publication 和 official grading。
- 主预算只用 wall-clock；记录 Codex 特有 turn/response 配置但不把它冒充共享 steps。

### MLE-010：Claude Code

- 与 Codex 共用受限 workspace、artifact 和 grader contract。
- 显式锁定 Claude Code CLI 版本和 permission configuration。
- `max_turns` 是内部上限，不能成为对其他 Agent 的共享预算。
- 增加真实 scored smoke 和 relay compatibility test。

### MLE-011：ML-Master 2.0

**需要中等重构**：

- 新增由 canonical request 生成 per-run config 的 builder，不再要求用户提供任意 YAML。
- builder 固定 public data、description、sample、workspace、output、模型、relay、GPU、CPU、timeout、embedding 和 grader endpoint。
- 去掉示例配置中的 task-specific path、双 GPU、local DeepSeek 等隐含默认。
- watchdog 使用统一 wall-clock 并保留 closeout 窗口。
- Agent workspace 只能看到当前 public task；禁止任意 host symlink/volume。
- final candidate 显式发布到统一 artifact contract。

### MLE-012：AiScientist

- 保留 native `aisci mle run`。
- 强制启用并解析 final submission validation，或者由 canonical host grader完全接管最终有效性。
- final validation 失败时 job 不能仍标 `succeeded`。
- 输出 job ID、真实 submission path、Agent profile/commit、final state 和 token usage。
- 验证传入 data root 最终只把 prepared public 复制到 solving workspace。

## 7. Terminal-Bench 36/53 AO 结构重构

当前 `BenchmarkAdapters/TerminalBench/adapter.py` 构造的是 89 题直接解题 Harbor job，不能通过小改参数变成 AO。这里必须恢复并重构独立的 AO architecture。

### AO-001：建立冻结的 Harness 基线

- 从锁定的 Harbor/Terminal-Bench source revision 提取 `terminus-2`。
- 保存 source URL、commit、tree hash、依赖 lock、初始 test/dev baseline 分数。
- 只允许编辑协议声明的 `terminus-2` agent、prompt、parser、tmux/session 路径。
- 初始 workspace 每个外层 run 从同一只读 snapshot 创建，不从上一个 Agent 的结果继续。

### AO-002：建立冻结 split 包

新增版本化 split package：

```text
terminal-bench-2/ao_protocol/
├── protocol.json
├── dev.json
├── test.sealed.json
├── split_manifest.json
└── SPLIT_CARD.md
```

要求：

- dev 精确 36 个唯一 task，test 精确 53 个唯一 task，并集等于冻结 89 题。
- split generator 只运行一次；正式实验读取冻结文件，不在每次 run 随机重分。
- manifest 记录 dataset digest、difficulty/category 分布、算法、seed、人工变更和 SHA-256。
- `test.sealed.json` 不挂载给 Agent workspace、shell、dev evaluator或 LLM host process。
- 如使用 reconstruction，protocol ID 和所有表格必须包含 `reconstruction`。

### AO-003：重写 `run_eval.py`

需要 host-owned evaluator：

- 输入 frozen harness snapshot 和 split identity。
- 对 split 中每题用 Harbor 0.20.0 运行**修改后的 terminus-2**，内层模型固定 `gpt-5.5`。
- concurrency 固定 8；task timeout 使用冻结 task config 和统一 multiplier。
- 收集每题 reward、error、agent exit、token/cost 和 Harbor lock/result。
- dev 模式可返回聚合分和允许的诊断；test 模式只在 outer search 结束后运行。
- final machine-readable 输出由结构化 JSON 文件提供，不从 stdout 最后一行正则猜分数。
- 缺 reward、exception、timeout 和 infrastructure failure 的计分规则在 protocol 中预注册。

### AO-004：恢复 Repository workspace 基础设施，但不恢复 profile 模拟 Agent

HEAD 中已删除的 `RepositoryAgent` 包含可复用概念：candidate workspace、external Git revision store、protected path、network-free evaluator sandbox、best materialization。应重新实现为 Agent-neutral 基础设施，例如：

```text
BenchmarkAdapters/TerminalAO/
├── adapter.py
├── contracts.py
├── protocol.py
├── split.py
├── evaluator.py
├── workspace.py
├── revisions.py
├── supervisor.py
├── results.py
└── launchers/
```

必须修正旧实现的问题：

- 旧 `shared-openai-repository-profile` 只是统一 OpenAI tool loop 加不同 prompt，不能代表 EAR、MLEvolve、ML-Master 2 或 AiScientist，禁止用于正式七 Agent比较。
- evaluator 不能只解析 stdout final line；必须验证 result schema、split digest 和完整 task count。
- candidate shell 的可写范围要用 allowlist，而不只是“仓库内任意路径”。
- test split 不以普通 `protected-path` 传入 Agent 进程；Agent 进程应完全不知道其 host path 和 task IDs。
- dev evaluator 每次在 disposable harness copy 上运行，不让 runtime artifact污染 candidate repository。
- best replay 只应用 allowlisted diff，拒绝 absolute symlink、escaping symlink、submodule、device file 和超大 artifact。

### AO-005：统一 outer supervisor

每个 Agent 使用同一个 supervisor 负责：

- 从 frozen baseline 创建 run workspace；
- 启动 host relay 和 dev evaluator broker；
- 强制 48 小时 wall-clock；
- 提供只允许 dev 的 evaluator capability；
- 周期性保存只读 telemetry，不干预 Agent；
- 在 Agent 退出/超时后选择其声明的最终 best；
- 校验 allowlisted diff，冻结 harness commit 和 tarball；
- 关闭所有 outer Agent 进程和 credential 后，调用一次 sealed test evaluator；
- 写 manifest、dev history、test report、token/cost 和完整失败状态。

### AO-006：dev evaluator broker

为了让七个原生 Agent 使用同一评分能力，提供一个 Agent-neutral 接口：

- CLI：`terminal-ao-eval --split dev --workspace ...`
- 可选 Unix socket/RPC：只接收 candidate snapshot identity，不接收任意 host path。
- 只允许 dev；test endpoint 在 optimization phase 不存在。
- 返回 schema 包含 pass rate、passed/failed/error counts、task-level dev evidence 和 evaluator digest。
- 每次调用记录 caller、candidate hash、开始结束时间和成本。
- 限制最大并发和调用频率，但不对不同 Agent施加不同隐藏限制。

## 8. 七个 AO 原生 launcher

### AO-AGENT-001：Codex

**最小复用**：使用原生 `codex exec` repository workflow。

- cwd 为隔离的 terminus-2 workspace。
- sandbox 只允许修改 editable allowlist。
- prompt 告知 48 小时目标、dev evaluator 命令和禁止访问 test。
- 锁定 Codex CLI、模型和 config；保存 session/trajectory。
- Agent 最终工作树经 supervisor 冻结，不允许 Codex 自行运行 test。

### AO-AGENT-002：Claude Code

**最小复用**：使用原生 `claude --print` repository workflow。

- 与 Codex 使用同一 workspace、allowlist、dev evaluator和 wall-clock。
- Bubblewrap/权限模式必须确保 bypassPermissions 不扩展到 host。
- 锁定 CLI 和模型；关闭 session persistence 或将 session 放在 run-owned目录。

### AO-AGENT-003：Arbor

**中等重构**：恢复 Arbor native repository AO，而不是 Harbor direct solver。

- 调用上游 Arbor coordinator/Agent repository workflow。
- 把 dev evaluator包装成 Arbor 原生 evaluator/tool，而不是替换 Arbor ReAct loop。
- 固定 coordinator cycles/depth 等内部配置并写入 manifest；主预算仍是 48 小时。
- 只把 final selected repository diff 交给 supervisor。

### AO-AGENT-004：AiScientist

**中等重构**：新增 domain-neutral repository optimization Subagent。

- 复用 `Subagent.run`、原生 LLM client、shell/file tools 和 token accounting。
- 新增 repository prompt、allowlisted shell/file interface、`run_dev_evaluation` 和 `complete_with_best` tool。
- 不复用 MLE submission、Kaggle candidate registry 或 Terminal direct-task prompt。
- supervisor 负责 final freeze 和 test，AiScientist 不能直接访问 test broker。

### AO-AGENT-005：EAR

**大重构，但保留算法**：为 EAR graph/KTS 增加 repository-domain backend。

- 一个 graph candidate 对应从 parent revision 创建的独立 terminus-2 sibling workspace。
- candidate action 生成/应用 repository diff，并在 disposable copy 上调用 dev broker。
- observation 是可信 dev pass rate，不是 candidate 自报 metric。
- sibling workspace 不能共享未提交文件、日志或 evaluator state。
- graph 选择出的 best revision由 supervisor重新从 frozen baseline replay，并重新跑 dev 验证。
- EAR 的 Thompson、graph update 和 parent selection 保持原生；只抽象 artifact、executor 和 evaluator contract。
- test reward 永不回流 graph。

### AO-AGENT-006：MLEvolve

**大重构，但保留搜索流程**：增加 domain-neutral repository candidate backend。

- 保留 Journal、draft/debug/improve/evolve、planner 和 selection。
- 把“生成单个训练脚本 + Kaggle metric/submission”抽象为“生成 repository patch + dev evaluation result”。
- 每个 node 使用隔离 revision/workspace，禁止多个 node 在同一宿主 workspace 累积修改。
- result parser 读取 broker JSON，不从 LLM 自报 metric 推断。
- final fusion 在 AO 中改为 best revision selection/replay；禁止拼接不兼容代码 patch。
- MLE-specific coldstart、submission fusion 和 format server 不进入 AO backend。

### AO-AGENT-007：ML-Master 2.0

**大重构**：把全部阶段迁移到 run-owned repository workspace 和 broker。

- config builder创建 repository-mode workflow，不依赖固定 MLE data root。
- research、coding、execution、review、selection 阶段都通过 workspace API 操作同一 candidate lineage。
- 每个实验有独立 revision和工作目录；并行实验不能污染 sibling。
- evaluator stage 只调用 dev broker；finalizer输出 best revision identity。
- 禁止任意 host volume/symlink；资源和 timeout 由 supervisor统一。

## 9. 有效比分与统计规则

### 9.1 主公平轨道

为了比较 Agent 架构而不是底层模型，主轨道固定：

- 外层模型：同一精确 model ID；当前计划为 `gpt-5.5`。
- reasoning effort：相同。
- temperature 和采样参数：相同，除非 Agent API 无法表达；差异必须预注册并单列 sensitivity。
- endpoint/relay 参数清洗、retry 和 timeout：相同。
- MLE：同一每题 wall-clock 和硬件。
- Terminal AO：同一 48 小时 outer wall-clock、同一 baseline harness和 dev broker。
- seed schedule：所有 Agent 使用同一列表，例如 `[0, 1, 2]`。

原生论文模型或不同模型可以作为独立 `native-model track`，不能与主公平轨道混成一个排名。

### 9.2 MLE 主分

每个 seed 分别计算：

- `Valid Submission Rate = valid / 22`
- `Above Median Rate = above_median / 22`
- `Any Medal Rate = any_medal / 22`
- `Gold Medal Rate = gold / 22`

主榜建议以 Any Medal Rate 为主要质量指标，同时完整报告其他三个 rate。逐题 raw score用于同题 Agent 比较。

跨 seed 报告 mean、sample standard deviation、SEM 和 95% CI。所有 22 题都进入分母；失败、超时、缺 submission 和 invalid submission 不能从分母删除。

### 9.3 Terminal AO 主分

每个 outer seed：

```text
Test Pass Rate = 53 个 held-out task 中 reward=1 的任务数 / 53
```

缺 reward、Agent error、verifier error 和 timeout 默认按 0 计入，但同时报告：

- solve failure rate；
- infrastructure error rate；
- clean Agent exit rate；
- test evaluator完整性。

只有 53/53 trial 都产生可审计终态，或按预注册规则填 0，run 才有有效 aggregate。跨 3 个 outer seed 报告 Avg@3、标准差和 95% CI。

### 9.4 效率指标

质量分之外并列报告：

- total/input/output/cache tokens；
- provider-confirmed cost 和 Harbor estimate，二者分开；
- outer wall-clock；
- dev evaluator调用次数和总 task-trials；
- CPU/GPU hours；
- MLE 搜索步数/candidates，Terminal AO revision/candidate 数；
- quality per million tokens、quality per hour作为辅助指标。

不能只报告质量/成本比而隐藏绝对质量。

### 9.5 Retry 和失败规则

- LLM request retry由统一 relay处理，最大次数固定并记录；重试消耗计入 token和 wall-clock。
- Agent bug、OOM、超时和无效 artifact不允许免费重跑。
- 只有预先定义的基础设施故障允许重跑，例如宿主断电、Harbor 自身 crash、确认的 provider-wide outage。
- 是否重跑由与 Agent 无关的自动规则决定，并保存原失败 run；不能看到分数后人工选择重跑。
- 正式表同时给出 intention-to-run 结果和允许重试后的 protocol-compliant结果。

### 9.6 禁止跨 Benchmark 合成未经定义的总分

MLE Any Medal Rate 与 Terminal AO Test Pass Rate 可以放在同一 scorecard，但当前不合成为单一数字。若未来需要总分，必须事先定义归一化、Benchmark 权重、seed聚合顺序和缺失值规则，并作为新 protocol version。

## 10. 公平性数据边界

本节只定义让两个 Benchmark 分数可比所需的最小边界，不假设 Agent 会主动作弊，也不设计额外的对抗性安全系统。

### MLE

- Agent 只见当前题 prepared public。
- private labels、leaderboard和 grader代码留在 host-owned scoring phase。
- 其他 Agent 源码、历史 run、研究报告和 token log不可见。
- credential只在 host relay；Agent只拿 run-scoped placeholder。

### Terminal AO

- Agent workspace 不含 test split、test IDs、test logs、test reward或 evaluator host path。
- dev evaluator和 test evaluator使用不同 capability；optimization phase根本不启动 test endpoint。
- evaluator在 disposable copy上运行 candidate harness。
- Agent 不能修改 Harbor、tasks、verifier、split、baseline record、relay和 evaluator。
- shell默认无 host credential、Docker socket和任意宿主文件访问。
- final harness进行 allowlist diff、symlink逃逸、大小和文件类型检查，确保七个 Agent提交的是同一类可 replay artifact。

## 11. 测试计划

### 11.1 单元测试

- 22-task membership和data digest。
- split 36/53互斥、完备、deterministic和manifest hash。
- score方向、invalid submission和aggregate公式。
- manifest/result schema和原子写入。
- artifact freshness、sample equivalence、symlink/device拒绝。
- retry分类和失败计零。

### 11.2 Contract 测试

- 七个 MLE launcher都能构建真实命令并返回显式 artifact path。
- 七个 AO launcher都调用对应原生 Agent loop。
- 同一 protocol config解析为相同模型、预算、hardware和retry。
- `benchmark_adapters` 与 `BenchmarkAdapters` 无行为分叉。
- direct Terminal smoke不能写入 AO result schema。

### 11.3 隔离测试

- MLE Agent读不到 private data、其他题、其他 Agent或历史结果。
- AO Agent读不到 test split和host secret。
- sibling candidate相互不可见。
- dev evaluator修改不能污染candidate或下一次评估。
- final replay只能产生allowlisted diff。

### 11.4 Synthetic AO acceptance

使用冻结的 36/53 task identity 和 synthetic evaluator 驱动真实 supervisor/launcher seam：

- 已知改动能提高dev和test；
- test不可见；
- 七个 Agent 的 shipped native launcher 都能创建 candidate、连接 dev broker并发布可 replay best；
- 失败candidate不污染best；
- test只执行一次；
- result和hash完整。

### 11.5 真实 smoke

- MLE：每个 Agent在同一低成本Lite题上跑短预算，必须得到官方 grader report或明确失败记录。
- AO：每个 Agent用冻结baseline在2个dev task上完成一次真实optimization seam smoke，再用2个非搜索task验证final freeze；这些smoke不进入正式表。
- relay、Docker、GPU、Harbor、proxy和artifact必须走正式路径，不能用mock替代。

## 12. 实施阶段与依赖顺序

### Phase 0：冻结协议和资产

- [x] 确认主公平模型和精确版本。
- [x] 冻结MLE-Bench 22-task set和split digest。
- [x] 生成并冻结`terminal-bench-ao-reconstruction-v1`。
- [x] 冻结Harbor、89-task dataset和terminus-2 baseline。
- [x] 写明failure/retry/seed/预算和统计协议。

### Phase 1：共享结果基础设施

- [x] 完成CORE-001到CORE-005。
- [x] 实现manifest、result、preflight、artifact和campaign schema。
- [x] 增加共享单元和contract测试。

### Phase 2：修复MLE全链路

- [x] 先修EAR和MLEvolve regression，恢复历史可用能力。
- [x] 接入官方grader和22-task campaign。
- [x] 接入Arbor、Codex、Claude Code、AiScientist。
- [x] 完成ML-Master 2 config builder和workspace隔离。
- [ ] 七 Agent逐个完成真实scored smoke。

### Phase 3：实现AO evaluator和supervisor

- [x] 完成AO-001到AO-006。
- [x] baseline terminus-2在36 dev和53 test上由同一 evaluator contract运行。
- [x] test sealing和one-shot gate通过公平性 contract 测试。

### Phase 4：接入较小AO launcher

- [x] Codex。
- [x] Claude Code。
- [x] Arbor。
- [x] AiScientist。

### Phase 5：接入搜索型AO launcher

- [x] EAR repository backend。
- [x] MLEvolve repository backend。
- [x] ML-Master 2 repository workflow。

### Phase 6：七Agent验收

- [x] synthetic AO acceptance与真实 Bubblewrap 隐藏性测试通过。
- [ ] 七Agent MLE真实smoke全部有有效report或预期失败记录。
- [ ] 七Agent AO seam smoke全部通过。
- [ ] 固定一个小型pilot campaign检查scorecard和统计脚本。

### Phase 7：正式实验

- [ ] 冻结全部commit、lock、image和protocol digest。
- [ ] 22 MLE tasks × 7 Agents × 3 seeds。
- [ ] 36/53 AO × 7 Agents × 3 outer seeds × 48h。
- [ ] 自动聚合、审计和生成最终横向表。

## 13. 已实现 CLI

```bash
# 分层 readiness 与正式协议 preflight
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters status
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters preflight

# 生成 MLE protocol、运行正式 cell、聚合
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters mle-protocol --output /runs/mle/protocol.json
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters mle-cell --protocol /runs/mle/protocol.json --agent ear --competition-id spooky-author-identification --seed 0 --data-root /data/mle-bench --campaign-dir /runs/mle
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters mle-aggregate --protocol /runs/mle/protocol.json --campaign-dir /runs/mle --agent ear

# Terminal AO 单 seed（supervisor 内部完成 optimize、freeze、一次性 test）与三 seed 聚合
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters terminal-ao --agent ear --protocol terminal-bench-2/ao_protocol/protocol.json --output-dir /runs/ao/ear/seed-0 --seed 0
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters terminal-ao-aggregate --protocol terminal-bench-2/ao_protocol/protocol.json --campaign-dir /runs/ao --agent ear

# 分栏 scorecard；不会生成 MLE+AO 混合总分
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters scorecard --mle-protocol /runs/mle/protocol.json --mle-campaign-dir /runs/mle --ao-protocol terminal-bench-2/ao_protocol/protocol.json --ao-campaign-dir /runs/ao --output /runs/scorecard.json

# 仅基础设施冒烟，明确不属于AO正式分
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters terminal-direct-smoke --agent codex --task fix-git --dry-run
```

`terminal-ao` supervisor 验证 optimization 已结束、final harness 已冻结、test 尚未消费，并在 test 启动前永久写入 `test_consumed=true`；同一 run 不能第二次测试。

## 14. 最终报告表

### MLE-Bench Lite

| Agent | Valid % | Above Median % | Any Medal % | Gold % | Avg Tokens | Avg Wall Time | Failures |
|---|---:|---:|---:|---:|---:|---:|---:|

另附22题逐题官方score和medal，以及跨seed方差。

### Terminal-Bench AO

| Outer Agent | Dev Pass Rate | Held-out Test Pass Rate Avg@3 | Std | 95% CI | Tokens | Dev Eval Calls | Infra Errors |
|---|---:|---:|---:|---:|---:|---:|---:|

表头必须写清 protocol ID：论文严格复现或 `terminal-bench-ao-reconstruction-v1`。

## 15. 完成声明模板

所有任务完成后，只能在满足以下模板时宣布 ready：

> 七个 Agent 均在冻结的 `<protocol id>` 下通过真实端到端 smoke。MLE-Bench Lite 使用相同的22题数据、模型、硬件、预算和官方 grader；Terminal-Bench 使用外层 Agent优化同一terminus-2、36 dev搜索、53 held-out test一次性评分的AO协议。所有正式run包含冻结commit、manifest、artifact hash、grader/evaluator结果、token/cost和失败记录。当前横向表不包含89题Agent直接解题分，也不拼接不同版本或seed的逐题最好结果。

在 Phase 6 完成之前，README 和 status 只能写 `planned`、`implementation-ready` 或 `smoke-ready`，不能写 `formal-ready`。

## 16. 历史离线验收记录

本节保存的是此前 contract/synthetic 回归和 review candidate 的记录，不是当前正式
协议的有效成绩证据。由于 MLE manifest、Terminal AO protocol 仍需升级到 schema-v2，
共享环境还缺 PyYAML，下面的 digest、测试结果和 scorecard 输出都必须在材料补齐后重新
生成，不能直接拿来发布横向排名。

### 16.1 冻结协议身份

- MLE protocol ID：`mle-bench-lite-official-22-v1`。
- MLE protocol digest：`883d83df01f597645bc9bec94e351b3f7bceac14de898e09b613c4319af3bc36`。
- MLE data manifest digest：`c82bb608db6ba0e64421d9bc23ef11f22d669c87db8201e55f5f7a742f0e276d`。
- MLE source commit：`507f92e1138bb6e40dac5c6ee7a6758e6424bf97`。
- Terminal AO protocol ID：`terminal-bench-ao-reconstruction-v1`。
- Terminal AO protocol digest：`2469cdd2354c645f6befa75a7bbf369ef3f689149bfcecca5648bdfd3037dda8`。
- Terminal dataset digest：`5e4ec407bec1a8ec35ae57c52aaa9e57fbafc7d79155d09555e54aca941e6cdd`。
- Terminal 36/53 split digest：`1a37db2d4a244cc8be356e3134620b2d0f396e8fdd9f6223ca84a12a3a6b54e4`。

### 16.2 自动验证

- `BenchmarkAdapters/tests`：完整测试 81 passed；随后固定 relay 的 GPT-5.5/high/temperature 1.0 后，adapter/core 定向回归 35 passed。
- MLE formal/adapter 定向测试：43 passed。
- Terminal AO core：11 passed。
- 七 Agent AO launcher/synthetic acceptance：18 passed。
- AiScientist model profile：5 passed；`gpt-5.5` 明确为 high reasoning、temperature 1.0。
- EvoMaster/ML-Master 2 reasoning config 与 MLEvolve shell syntax smoke：通过。
- 两次 `status`、`preflight`、`scorecard` 输出逐字节一致；scorecard 包含七个 Agent、MLE 22×3 固定分母、AO 53×3 固定分母、失败计零，并保持 `composite_score=null`。
- 本轮验证没有实际调用收费模型 API；四次正式入口都在启动 relay/API 前由 dirty-source gate 拒绝。后续真实 smoke 和长跑只应在明确批准 API 预算后执行。

证据保存在 `/tmp/grok-goal-a4a338243a3c/implementer/`：`core-tests.log`、`mle-tests.log`、`terminal-ao-tests.log`、`seven-agent-ao-tests.log`、`cli-run-1.json`、`cli-run-2.json` 以及四个正式入口 launch log。

### 16.3 当前可声明状态

- 目前只有部分 Agent 的命令可以构造；Arbor、ML-Master 2.0、AiScientist 还需要显式 variant 或 clean 原版路径。即使命令可以构造，也不代表已经可以正式评分。
- 当前仓库中 EAR、MLEvolve、Arbor、ML-Master 2.0、AiScientist 有未提交变化；正式入口会直接返回失败，不会开始正式实验。
- `BenchmarkAdapters/.venv` 当前缺 PyYAML；MLE manifest 仍是 schema 1，Terminal AO protocol 仍是 schema 1，模型配置仍是占位符。
- 当前可以生成**结构正确、失败保留分母的横向 scorecard**，但里面的零分是“正式结果缺失计零”，不是七个 Agent 已完成的实验成绩。
- 只有在干净 commit 上完成七 Agent × 至少 3 seeds 的真实长跑后，才能发布有效正式比分并把 readiness 升级为 `formal_protocol_ready`。
