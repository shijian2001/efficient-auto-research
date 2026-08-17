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
