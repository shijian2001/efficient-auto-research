# 七、G4 失败、G5 实验基础设施与当前协议（2026-07-19→22）

> 承接[文档六](06_stagnation_heated_ts_and_full_six_tasks.md)。文档六结束于 G3，并用不同
> 版本的每题历史最好成绩构成诊断性六题汇总。本文记录 G4 的失败、为什么不能继续靠行为过滤
> 提升稳定性，以及 G5 如何在不改变 G3 搜索行为的前提下加固实验可信链。
>
> **当前状态（2026-08-16）**：本文仍是 G4/G5 历史基础设施记录。当前七 Agent × 两个目标
> Benchmark 的 Adapter 代码大体已写，但 MLE schema-v2 manifest、Terminal AO schema-v2
> protocol、真实 model-track 配置、Adapter Python 依赖、干净 Agent source 和真实 scored
> smoke 尚未全部准备好，因此没有正式横向分数。以
> `BenchmarkAdapters/docs/SEVEN_AGENT_BENCHMARK_REPAIR_PLAN.md` 的清单为准。

## 1. 代际定位

| 代际 | commit | 搜索行为 | 结论 |
|---|---|---|---|
| G3 | `7cd9ed5`（含 `5d1ca2a`） | metric direction + stagnation-heated KTS | 已有历史强结果，但六题不是同一版本 |
| G4 | `7be8152` | 检测并排除 behavior no-op/duplicate 节点 | 六题统一运行明显退步，策略失败 |
| G4 revert | `212870f` | 恢复 G3 search/best/ensemble eligibility | 作为 G5 基点 |
| G5 | `a6acc90` | G3-compatible；behavior 仅 telemetry | 基础设施 closeout，尚无官方性能结果 |

G5 分支是 `ear/g5`，worktree 是
`ear-worktrees/attempt-isolation-telemetry-v2`。分支上的后续文档提交不等于算法变化，正式
运行仍需记录精确 commit。

## 2. G4 想解决什么

G4 的出发点是一个真实现象：某些子节点与父节点的 prediction distance 接近零，但本地
metric 却变化较大。直觉上，这类节点可能是 no-op、重复方案或 stale submission，不应该继续
占用搜索预算。

G4 将这种检测直接用于搜索资格、best selection 或 ensemble 路径。问题在于它把“观测信号”
升级成了“控制信号”，而 prediction equivalence 并不能证明方案没有搜索价值：

1. 相同 prediction 可能来自不同代码路径，后续可演化性不同。
2. 数值容差、验证 split 和 metric 抖动会使分类有不确定性。
3. 提前排除节点改变 KTS 的图、观测和可达父节点，影响会在后续步骤放大。
4. 当 artifact provenance 本身不够严格时，行为比较可能在比较错误归属的文件。

六题运行表明，这个控制策略没有稳定改善，且出现显著倒退。因此 `212870f` 完整撤销其搜索
影响，不再把 G4 当作有效代际。

## 3. 真正暴露的基础设施问题

G4 诊断进一步暴露了一个更基础的问题：旧执行链允许多个 step 围绕共享 workspace 和终态
submission 工作。若新脚本没有生成文件，旧文件可能与新 stdout metric 被错误配对。即使多数
运行没有触发，缺乏严格 provenance 也会让 no-op/duplicate 归因无法令人信服。

因此 G5 的目标不是“更聪明地过滤节点”，而是回答：

> 这个 metric、prediction 和代码是否确实来自当前 run 的当前 attempt？

## 4. G5 设计

### 4.1 run 和 attempt 隔离

- 每个 run 独占 `workspace/runs/<run_id>/`，同 run ID 拒绝覆盖。
- 每个 scheduled step 独占 `attempts/step_<n>_<uuid>/`。
- LLM 生成代码只看到稳定相对路径 `submission.csv`；UUID 和绝对路径不进入 code hash 或 embedding。
- step 执行前检查真实目标状态，不允许复用上一步文件。

### 4.2 artifact 可信链

- 校验普通文件、mtime、列、完整/唯一 ID、行数和 prediction 内容。
- 数值列必须 nonempty + finite；文本列允许普通文本和空字符串，兼容 Chaii。
- 用临时 SQLite 做 bounded-memory ID 校验、canonical fingerprint 和行为比较。
- 校验后原子冻结 controller-owned read-only artifact，后续消费前复核 SHA-256。
- 最终 outer submission 只能由 trusted best artifact 原子发布。
- evaluator 只有在进程 exit 0 且 outer hash 与当前 run report 完全一致时才允许官方评分。

### 4.3 provenance 与可复现性

- `--seed` 初始化 Python/NumPy 搜索随机性；LLM 本身明确不保证确定。
- report 保存 canonical config、config SHA-256、model、temperature、时间限制和 seed。
- Git commit/branch/dirty 由宿主 launcher 注入，容器内 Git 只作 fallback。
- host `launch_manifest.json` 记录 competition、模型/effort、steps/timeout、GPU/CPU、data path、
  agent commit/dirty、launcher/relay hash 和 container image，不记录 credential。
- report 独立记录 `launch_manifest_sha256`，不与 config hash 混用。

### 4.4 telemetry 不控制搜索

G5 恢复 G4 的 behavior/no-op 检测，但只作观测：

- `behavior_status` 与 `metric_consistency` 分离。
- 相同 prediction、不同 metric 记为 `noop/duplicate + inconsistent`，不是 effective。
- classification 本身无副作用；fingerprint owner 只在 graph add 成功后提交。
- embedding/graph-add 失败仍记录已完成的 artifact telemetry，但不留下 owner。
- no-op/duplicate 节点与 G3 有相同 search、best 和 ensemble eligibility。

## 5. 温度与搜索行为

G5 明确保持 G3 的实际 LLM temperature `1.0`，并在 metric-sign、plan、code 三类调用中显式
传入。CLI、canonical config 和 report 一致。

`thompson.py` SHA-256：

```text
2c52d6b392b53438ec5b85842f34acdaaafe4e81719fcd34e127b5fb96f62292
```

它与 G3 byte-identical。G5 没改 breadth、depth 或 KTS。

## 6. Closeout 验证

G5 closeout 完成：

- 33 个 pytest 测试通过；
- Python 编译、`bash -n`、`diff --check` 通过；
- 两步 mocked `run.main` 覆盖 LLM、执行、冻结、best 和 outer publish；
- 覆盖 step 1 成功/step 2 不产文件、embedding/graph-add 失败 owner 不污染、同 seed parent sequence、
  Chaii 空文本、evaluator timeout/nonzero 不 grading、new/old report schema；
- worktree clean 后提交并创建 `ear/g5`；
- 未实现 controlled evaluator，未跑真实 smoke 或正式 benchmark。

## 7. 当前实验规则

1. 先做最小真实 smoke，不把 smoke 分数写入主结果。
2. 正式六题必须来自同一 frozen G5 commit 和预声明配置。
3. 监控只读，不修改代码、不重启、不补跑、不提前评分。
4. 进度表中的 EAR `best_metric` 必须标为本地指标。
5. 跑完后先验证 outer/report hash，再做官方 grading。
6. 只有 G5 官方分数才能与 MLEvolve 官方分数计算差值和胜负。
7. 不得拼接 G0–G3 历史最好值构造 G5 成绩。

完整操作清单见
[G5 Experiment Protocol](../ear-worktrees/attempt-isolation-telemetry-v2/docs/EXPERIMENT_PROTOCOL.md)。

## 8. 尚未完成

- launcher/container/relay/GPU 的最小真实 smoke；
- 历史硬编码 API credential 的服务端撤销确认；
- 宿主 launcher/relay/plot 的 Git 版本化；
- G5 单一版本六题完整运行和官方评分。

结论：G5 的代码级实验底座已经完成，但还不能声明 G5 性能提升。下一条有效性能结论必须来自
完整、独立、可追溯的 G5 正式运行。

## 9. 12h 正式 campaign 前的 per-cell 证据审计（2026-08-27）

在 7 agent × 6 task × 12h 正式 campaign 启动前，对当天已完成的六个真实 cell
（codex / claude-code / ai-scientist / ear / mlevolve / arbor，覆盖全部四条 adapter 路径）
做了一次「事后能否重建 agent 轨迹」的证据审计。

### 已修复

`BenchmarkAdapters/process.py` 的 `run_command` 在 `subprocess.TimeoutExpired`
分支上直接丢弃了 `exc.stdout`，超时 cell 因此完全没有 `agent.log`。
证据：`/tmp/mlm_campaign_v4/ml-master-2/seed-0/detecting-insults-in-social-commentary/`
（`status: "timed_out"`）目录下没有 `agent.log`，只有 `result.json` 和 `manifest.json`。
12h 预算下超时是最可能的结束方式，这正是最需要轨迹的一类 cell。
现在超时路径会把 deadline 之前的 partial stdout 经同样的 `redact_process_output`
写入 `agent.log`；正常路径行为不变。

### 未修复但已记录的缺口（按危害排序）

1. `result.json` 的 `tokens` / `cost` 恒为 `{}`，尽管 per-call telemetry
   （`token_usage.jsonl` / `relay-telemetry/*.jsonl`）齐全且可求和。
   `campaign.py` 的 aggregate 因此把每个 cell 的 usage 记为 `None`。
2. claude-code 的 `agent.log` 只有 1885 B 且不含任何轨迹：`_workspace_command`
   用 `--print --bare` 而没有 `--output-format=stream-json --verbose`，
   而同路径的 codex 产出 165 888 B 的完整 exec trace。
3. EAR 的 `report.json` 记录 6 步全部失败、`best_metric: null`、
   "Ensemble skipped: only 0 scored submissions"，最终却提交了 0.91662 的
   submission.csv，日志无法说明该文件由哪一步产生。
4. MLEvolve 的节点代码只存在于 `MLEvolve.verbose.log` 文本中，
   workspace 未落盘任何 node 代码文件。

### 体积与泄漏

- 无 API key 泄漏：环境中的 `OPENAI_API_KEY` / `ANTHROPIC_AUTH_TOKEN` /
  `CUSTOM_API_KEY` 在六个 cell 中均无命中；`auth.json` 只含占位串 `"proxy"`。
  `redact_process_output` 对 env 派生 secret 和 `Authorization:` 行均生效。
- 无 private label 泄漏：未发现 private/test 标签或 answers 文件路径。
- 体积主项不是日志而是 `agent-output/cache/huggingface`：EAR 单 cell 419 MB
  （`BAAI/bge-base-en-v1.5` 权重 438 MB），42 cell 外推约 17 GB，
  而 `/` 仅剩 103 GB。日志本身 12h 外推最大约 121 MB
  （codex `state_5.sqlite-wal`），可接受。

---

## 08 · 单次调用型 CLI 的预算真空（2026-08-30）

### 现象

7-agent × 3-task campaign（`20260828_223600_..._r2_portsafe`，round 02–07）
跑完后，用时分布是两极的，中间没有任何一个 agent：

```
mlevolve      12:00 12:00 12:00   跑满
ml-master-2   12:00 12:00 12:00   跑满
arbor         10:47 10:47 10:47   自己收尾（89.9%）
─────────────────────────────────────────
claude-code    0:29  0:21  0:31   约 4%
codex          0:13  0:12  0:06   约 1~2%
```

codex 和 claude-code **不是崩溃**：日志末尾是正常收尾陈述，提交有效、
分数正常（claude-code mlsp 0.90645 银牌，用时 21 分钟，比 arbor 跑
10h47m 的 0.90201 还高）。它们是**主动早退**。

### 根因

不是 `--max-turns`。`campaign.py:322` 传的是 `max_turns=1000`，
`adapter.py` 里的 `8` 只是 CLI 默认值，正式路径从不使用它。

真正的原因是**任务指令里没有时间这回事**。冻结的
`task_specs/mle-bench-lite.md` 全文只要求 "Produce exactly one
`submission.csv`"，没有提预算、没有提迭代。codex 照做了，然后退出——
它的行为完全正确。

结构性差异在于预算的传递方式：

| | 预算怎么到达 agent |
|---|---|
| arbor / ear / mlevolve / ml-master-2 / ai-scientist | `--budget-seconds`，自带搜索循环对着它迭代 |
| codex / claude-code | **无通道**。单次 `subprocess` 调用，返回即结束，且看不到自己的启动时刻 |

### 对照 Frontis-MA1 / NatureBench

Frontis 的 `agent/claude.py`、`agent/codex.py`（NatureBench 公开实现）
证实：**没有任何"跑满"的代码机制**——无 `--max-turns`、无
`subprocess timeout=`、无外部重调循环，单次调用。填满预算全靠三样东西：

1. prompt 明说 `"Use the full time budget: keep iterating... until
   /time_remaining is close to zero"` / `"Do not exit early"`
2. 一个可查询的时钟：`/time_remaining` 端点
3. 可反复调用、取最大值的 `/evaluate` 服务，`"a worse later attempt will
   NOT override an earlier higher score"`

注：Frontis 的 MLE 专用 `claude.py/codex.py` 未公开，上述来自同一批人的
NatureBench 实现，机制可借鉴但不能声称是同一份文件。

第 3 条在 MLE-Bench 上**不能照搬**：私有标签必须对 agent 保密，开打分
接口等于泄题。替代做法是把闭环的构建推给 agent 自己（切 CV、用竞赛
自身指标做反馈），这正是 mlevolve/ml-master 内部本来就在做的事。

### 改动（commit `e3e541f`）

新增 `task_specs/mle-bench-lite.cli-harness-addendum.md`，**只追加到
codex/claude-code 的 prompt，不进共享 spec**：

- 声明单次调用语义：不会被重启，退出即结束，submission 可覆盖
- 声明预算：`{budget}` / `{budget_seconds}` 由 cell 的
  `timeout_seconds` 渲染，smoke run 不会谎称 12 小时
- 要求先落 baseline 保底，再持续改进直到预算将尽
- 要求自建验证闭环（切 CV、用竞赛指标判断），因为 grader 分数在退出前不可见
- 早停的唯一合法条件：自评分数连续多次实质性尝试无改善

配套 `workspace/DEADLINE.txt`：一个 unix 时间戳，`date +%s` 一比即得剩余
秒数。prompt 可以写明时长，但只有磁盘上的数字能撑过 12 小时而不依赖估算。

codex 额外加一句 session rule：`codex exec` 在无 tool call 的纯文本回复
处结束会话——round-04 那句 "Created the required submission.csv" 正是
这种结尾。claude-code 没有这条规则，因此不告诉它有。

### 公平性

共享 spec 保持逐字节不变，`task_spec` digest 仍是
`2792c228...453c7780`，`protocol_digest` 不动，**已评分的格子provenance
不受影响**。native agent 读的 `AGENT_TASK.md` 也未改动——它们已经通过
`--budget-seconds` 拿到同一份预算，再塞一份散文副本等于给半边战场换了
任务描述。

测试（`test_mle_formal.py`，4 项）钉住：digest 不变、addendum 不泄露任何
任务内容（competition 名、数据线索）、预算按真实 cell 渲染、native
workspace 未受影响、session rule 只发给 codex。

### 尚未解决

- **重跑范围**：codex / claude-code 共 6 格需要在新 prompt 下重跑。
  其余 5 个 agent 的格子不受影响（spec 未变）。
- 这一改动**不改变**上一条记录里的 timeout 不评分缺陷：EAR 跑满 12h 后
  被判 `timed_out` 直接抛错、不走评分路径，而它的
  `agent-output/submission.csv` 经官方 grader 离线补评为
  spooky 0.27628 铜牌 / mlsp 0.88328 铜牌。该路径仍待修。

---

## 09 · 超时格子不评分：只惩罚勤奋 agent 的测量缺陷（2026-08-30）

### 根因

`formal.py:57` 的 `run_formal_mle` 是一条**无 try/except 的直线**：

```
① MleLiteAdapter.run()            跑 agent
② publish_artifact()              拷到 artifacts/final/
③ grade_submission()              官方评分
④ BenchmarkRunResult(COMPLETED)   写结果
```

超时时 `run_command`（`process.py:150`）抛
`AdapterError("... timed out")`，异常从第 ① 步冲出，**②③④ 全部跳过**。

`campaign.py` 的兜底分支接住它，然后去
`artifacts/final/submission.csv` 找提交——**那个位置正是被跳过的第 ②
步负责填的**。必然为空，必然记 `score: null`。

### 为什么这是系统性偏差

哪种 agent 会超时？恰恰是把 12 小时用满的那种。提前收工的进程正常退出、
四步走完、分数照拿：

```
codex        6 分钟退出   → 正常路径 → 0.33465 计入
claude-code  31 分钟退出  → 正常路径 → 0.35189 计入
ear          跑满 12h    → 抛异常   → null（尽管交了铜牌）
```

**越努力越吃亏。** 而 commit `e3e541f` 刚把 codex/claude-code 也改造成
"跑满预算"型，不修的话下一轮重跑很可能 6 格全 null。

### 证据（EAR round-01）

```
spooky:  submission 写于 14:02:24，deadline 15:29:42，早 1h27m
         result.json 写于 15:31:43 记 timed_out/null
         artifacts/final 0 文件，grading 0 文件
mlsp:    submission 写于 14:15:10，早 1h14m，同样记 null
```

官方 grader 补评：spooky **0.27628 铜牌**、mlsp **0.88328 铜牌**，
`valid_submission: True`，且都通过 `_sample_hashes` 反作弊比对。

### 改动

**新状态** `RunStatus.TIMED_OUT_SCORED`（`records.py`）：分数有效、计入
牌位，但状态名保留"没在预算内自己收尾"这个事实，`completed` 会把它抹掉。
配套 `is_scored` property，`validate()` 按它分支而非硬编码 COMPLETED。

**救援逻辑** `campaign.py:_rescue_timed_out_submission`：超时且
`artifacts/final/` 为空时，去 `submission_roots()` 找 agent 实际写的提交，
补做被跳过的 ②③ 步。复用现有件，未新写任何校验。

四条不放松的约束（全部实测验证）：

| 场景 | 结果 |
|---|---|
| sample 原样交回 | 拒（反作弊 `_sample_hashes`） |
| deadline 之后才落盘 | 拒，且不留下 artifacts |
| 没交 / 空文件 | 拒 |
| 垃圾内容 | 官方 grader 拒，记 `rescue declined: ...` |
| deadline 前的合格提交 | 评分，`TIMED_OUT_SCORED` |

**聚合放行**：`campaign.py` 的 `if status != COMPLETED` 分支会把新状态
当失败、把 report 记成 `None`，分数仍进不了成绩单。改为
`status not in {COMPLETED, TIMED_OUT_SCORED}`，让救援格子走**已评分**
路径——artifact hash 绑定、grader report digest 绑定与正常完成完全相同。
`aggregate.py` 无需改动（它按 `reports[task_id]` 计数）。

### EAR 两格补评

用修好后的同一段代码重跑 `publish_artifact` + `grade_submission`，产物与
将来原生跑出来的同构：

```
spooky  timed_out_scored  0.27628  铜牌  sha256 6c32380b0a525833...
mlsp    timed_out_scored  0.88328  铜牌  sha256 c0351e017a77fef3...
```

原 `result.json` 备份为 `result.timed-out-unscored.json.bak` 保留证据。
两格 artifact hash 与 result 记录一致（已核对）。

**EAR 因此从"0 格有效"变成"2 格铜牌"**，jigsaw 那格仍是真失败（OOM
exit 137，`agent-output` 无提交）。

### 测试

`test_mle_formal.py` 5 项 + `test_mle_formal_evidence.py` 2 项：
救援成功给分、反作弊不放松、deadline 后不采纳、什么都没交仍记 0、
grader 说了算、救援格子进成绩单、**篡改救援格子的 artifact 仍报
`artifact hash mismatch`**（新状态不是绕过绑定的后门）。

MLE 套件 46 项全过。全量 `BenchmarkAdapters/tests/` 失败数 81 项，与改动
前完全一致（均为 `test_optimizer_design.py` 等无关套件的预存失败），
通过数 225 → 232。
