# 四、12 小时长跑结果与 MLEvolve 效率优势归因

> 涵盖：2026-06-01 三题 12 小时运行结果、efficient-auto-research 与 MLEvolve 的有效尝试率对比、代码与搜索机制归因、问题清单、改进优先级。
>
> 📜 **历史存档**。归因分析仍是 EAR 改进的依据；第 8-9 节建议的前 3 项
> （code extraction / compile gate / submission validation）已落地 EAR `proxy-based-eval` 分支。
> G5 已进一步完成 attempt/run 隔离和 artifact 可信链；当前状态见
> [文档七](07_g4_failure_and_g5_infrastructure.md)。本文中的问题清单保留为历史归因。
>
> 本文记录的是历史实验，不代表当前 7 Agent × 2 Benchmark 正式评测已经完成；当前状态以
> `BenchmarkAdapters/docs/SEVEN_AGENT_BENCHMARK_REPAIR_PLAN.md` 为准。

---

## 1. 背景

前期 `spooky-author-identification` 的 5 步和 30 分钟实验显示，efficient-auto-research 在短时间预算下更快拿到可用分数，主要优势来自调用链短、代码更精简、没有 MLEvolve 的 draft 冷启动。

但 2026-06-01 的三题 12 小时长跑结果显示：在更长时间预算下，MLEvolve 的整体效率和最终结果反而更稳。这里的“效率”不是指单次执行耗时更短，而是指单位时间内产生有效、可验证、可继续利用实验节点的能力。

本次复盘聚焦两个问题：

1. 为什么 MLEvolve 在 12 小时预算下比 efficient-auto-research 更高效？
2. efficient-auto-research 应该优先修什么，才能减少无效尝试浪费？

---

## 2. 实验口径

### 运行批次

运行批次：`20260601_221959_12h_3task_8gpu`

题目：

| 题目 | 难度 | 指标方向 |
|------|------|----------|
| `chaii-hindi-and-tamil-question-answering` | Medium | 越高越好 |
| `jigsaw-toxic-comment-classification-challenge` | Low | 越高越好 |
| `mlsp-2013-birds` | Low | 越高越好 |

Agent：

| Agent | 简称 | 方法 |
|-------|------|------|
| efficient-auto-research | EAR | Kernel Thompson Sampling + full-script regeneration |
| MLEvolve | MLEvolve | Monte Carlo Graph Search + staged agents |

### 数据统计口径

efficient-auto-research：

- 从 `report.json` 和 `traces/*.json` 统计。
- 有效尝试定义为：有 `metric` 且没有 execution error。
- 错误类型从 trace 的异常与 stdout/stderr 聚合。

MLEvolve：

- 从 `journal.json` 统计。
- 去掉 root node。
- 有效尝试定义为：`is_valid=True`。
- buggy/error 节点包括执行失败、缺 metric、缺 submission、格式或内容校验失败。

官方成绩：

- 使用 MLE-Bench official grading 对最终 submission 评分。
- MLEvolve 的 `mlsp-birds` 另外检查了保存的 top candidates，发现 top2 official score 高于最终 selected best。

---

## 3. 最终成绩

| 题目 | Agent | 最终 official score | 结果 |
|------|-------|---------------------|------|
| chaii | efficient-auto-research | **0.69717** | valid，无 medal |
| chaii | MLEvolve | 0.69145 | valid，无 medal |
| jigsaw | efficient-auto-research | 0.98565 | valid，above median，无 medal |
| jigsaw | MLEvolve | **0.98638** | valid，above median，无 medal，接近 bronze |
| mlsp-birds | efficient-auto-research | 0.90708 | valid，silver |
| mlsp-birds | MLEvolve selected best | 0.92889 | valid，silver |
| mlsp-birds | MLEvolve top candidate | **0.93386** | valid，silver |

结论：

- efficient-auto-research 在 `chaii` 赢 MLEvolve：`0.69717` vs `0.69145`。
- MLEvolve 在 `jigsaw` 小幅领先：`0.98638` vs `0.98565`。
- MLEvolve 在 `mlsp-birds` 明显领先：selected best `0.92889`，top candidate `0.93386`，efficient 为 `0.90708`。
- 因此，不能简单说 MLEvolve 每题都更强；更准确的结论是：12 小时预算下，MLEvolve 的有效实验利用率明显更高，最终整体结果更稳。

---

## 4. 有效尝试率对比

| 题目 | Agent | attempts / nodes | 有效 | 有效率 | buggy/error | timeout | SyntaxError |
|------|-------|------------------|------|--------|-------------|---------|-------------|
| chaii | EAR | 340 | 43 | 12.6% | 297 | 0 | 282 |
| chaii | MLEvolve | 57 | 34 | 59.6% | 23 | 6 | 0 |
| jigsaw | EAR | 30 | 18 | 60.0% | 12 | 4 | 8 |
| jigsaw | MLEvolve | 32 | 9 | 28.1% | 23 | 16 | 0 |
| mlsp-birds | EAR | 170 | 25 | 14.7% | 145 | 5 | 58 |
| mlsp-birds | MLEvolve | 69 | 27 | 39.1% | 42 | 9 | 0 |

三题合计：

| Agent | 总 attempts / nodes | 有效数 | 有效率 | 主要浪费 |
|-------|----------------------|--------|--------|----------|
| EAR | 540 | 86 | 15.9% | SyntaxError、路径错误、格式/metric 不可靠 |
| MLEvolve | 158 | 70 | 44.3% | timeout 和长执行 |

最关键的差异是：efficient-auto-research 不是跑得慢，而是大量尝试在进入真正实验前就失败了。尤其 `chaii` 和 `mlsp-birds`，很多 step 几乎即时失败，token 和 step 数没有转化成有效模型实验。

---

## 5. Best 到达时间

| 题目 | Agent | local best | 到达时间 | 最终 official score |
|------|-------|------------|----------|---------------------|
| chaii | EAR | 0.674391 | step 266，9.99h | 0.69717 |
| chaii | MLEvolve | 0.6927 | step 51，10.74h | 0.69145 |
| jigsaw | EAR | 0.990686 | step 10，6.00h | 0.98565 |
| jigsaw | MLEvolve | 0.991446 | step 32，11.73h | 0.98638 |
| mlsp-birds | EAR | 0.847677 | step 27，3.76h | 0.90708 |
| mlsp-birds | MLEvolve | 0.9182 | step 66，10.88h | 0.92889 / 0.93386 |

观察：

- EAR 在 `jigsaw` 很早达到接近最优，这说明它的短跑能力仍然强。
- EAR 在 `mlsp-birds` 早早 plateau，后续大量尝试没有有效转化。
- MLEvolve 的 best 通常来得晚，但它能持续在有效分支上做 exploitation，因此 12 小时后更容易拿到稳定提升。

---

## 6. 根因分析

### 6.1 代码抽取：EAR 太宽松，MLEvolve 有编译门禁

efficient-auto-research 的代码抽取逻辑位于：

`mle-bench-agents/efficient-auto-research/agent/engine/search.py`

其 `_extract_code()` 在没有抽到 fenced code block 时，会直接返回整段模型回复。实际 trace 中多次出现把自然语言分析文本当成 Python 执行的情况，例如首行类似：

```text
Looking at the current code...
```

这直接导致大量 line 1 `SyntaxError`。

MLEvolve 的代码抽取和校验位于：

`baselines/MLEvolve/utils/response.py`

它会在抽取后使用 `compile()` 校验，并配合格式化和 retry，明显减少自然语言、diff marker、markdown 残留被送去执行的概率。

影响：

- EAR 三题约 348 次 SyntaxError，其中 `chaii` 约 282 次。
- MLEvolve 的语法类浪费远低于 EAR。
- 这是本轮长跑中最直接、最应该优先修复的问题。

### 6.2 执行前门禁：EAR 直接 subprocess，缺少便宜失败过滤

EAR 的 executor 直接把脚本写入 workspace 并 subprocess 执行，缺少以下前置检查：

- `compile()` 或 `py_compile`。
- markdown fence、SEARCH/REPLACE marker、自然语言首行扫描。
- 基础路径预检。
- submission 文件存在性预检。

因此很多可以在毫秒级拦下的问题进入完整 step 流程，污染 trace、浪费 token，并且让搜索策略收到低质量反馈。

MLEvolve 的生成和执行链路更严格，会把明显不可执行的候选拦在执行前或标记为 buggy，减少进入 exploitation 的概率。

### 6.3 数据预览：EAR 文件上下文太浅

EAR 的数据预览主要展示顶层 CSV 和少量文件名，缺少递归文件树和 sample submission 强提示。

本轮 `mlsp-birds` 中反复出现：

- 猜错 `sample_submission` 路径。
- 把 `rec_id` 表头当整数解析。
- 对嵌套目录结构判断不稳定。

MLEvolve 的 `utils/data_preview.py` 会生成更完整的文件树，并强调 sample submission 是格式权威。这能降低路径猜测和格式猜测带来的无效尝试。

### 6.4 结果解析：EAR 过度依赖自报 METRIC

EAR 的 best 更新主要依赖 stdout 中的：

```text
METRIC=<number>
```

问题：

- 没有强制检查 submission 是否存在。
- 没有 official format validation。
- 没有内容质量检查，例如空提交、常量预测、placeholder。
- 没有 metric direction 校验。
- 内部 CV spike 可能主导搜索，但与 official score gap 很大。

MLEvolve 的 result parse 和 validation 更完整：

- 执行错误、无 metric、无 submission 会标为 buggy。
- 会判断 metric direction。
- 会做 submission 格式校验。
- 会做内容质量检查。
- 会保存 best 和 top candidates，便于后续复核。

### 6.5 搜索结构：MLEvolve 的节点有状态，EAR 的失败反馈太弱

MLEvolve 的搜索不是单纯选择一个 parent 生成完整脚本，而是按节点状态路由：

| 节点状态 | MLEvolve 行为 |
|----------|---------------|
| root | draft agent |
| buggy / invalid | debug agent |
| valid / promising | improve agent |
| branch stagnant | evolution agent |
| 后期融合 | fusion / aggregation |

它还会：

- 使用 UCT 进行前期探索。
- 后期切到 Top-K exploitation。
- 限制同一 branch 占比，保持 branch diversity。
- 对结果进行 backpropagation，更新 visits 和 reward。
- 对 buggy 分支做 debug 子节点限制。

EAR 使用 Kernel Thompson Sampling 选择 parent，但失败节点的负反馈主要是 prompt 中的 error warning。失败 attempt 通常不作为强负样本进入搜索模型，因此相同失败模式会反复出现。

### 6.6 候选管理：MLEvolve 的 best 是搜索输入，EAR 的 best 主要是最终输出

MLEvolve 会保存：

- `best_solution/`
- `best_submission/`
- `top_candidates`
- 每个 node 独立 submission，例如 `submission_<node_id>.csv`

这些候选会反过来参与 Top-K、fusion、aggregation。

EAR 主要维护一个全局 `best_attempt/best_metric`，最后 rerun best code。每个尝试写同一个 `submission.csv`，如果没有强校验，容易出现覆盖、rerun 不一致、local metric 与最终 submission 不一致等风险。

本轮 `mlsp-birds` 中，MLEvolve 的 top candidate official score `0.93386` 高于 selected best `0.92889`，说明候选池保存本身就有价值。如果只保留单一 best，容易丢掉更好的 submission。

---

## 7. 与短跑结论的关系

前一轮 `spooky` 5 步和 30 分钟实验中，EAR 明显优于 MLEvolve。该结论仍然成立，但适用条件是短预算。

短预算下：

- EAR 调用链短。
- 每步代码少。
- 没有 draft 冷启动。
- 能快速拿到 baseline 分数。

长预算下：

- EAR 的 full-script regeneration 会不断产生语法、路径、格式类失败。
- 缺少严格验证导致搜索信号噪声大。
- 缺少 structured debug 和 candidate pool，后期 exploitation 不足。
- MLEvolve 虽然启动和单步开销更大，但后期更能持续利用有效分支。

因此当前更完整的判断是：

| 时间预算 | 更占优的 Agent | 主要原因 |
|----------|----------------|----------|
| 5 步 / 30 分钟 | EAR | overhead 低，快速 baseline |
| 12 小时 | MLEvolve | 有效尝试率高，验证和搜索闭环更完整 |

---

## 8. 问题清单

### P0：必须先修

1. 严格代码抽取
   - 必须抽到 Python code block。
   - 没抽到时重新要求 code-only。
   - 禁止直接执行 raw response。

2. 执行前编译门禁
   - 对生成脚本运行 `compile()` 或 `py_compile`。
   - 拒绝 markdown fence、自然语言首行、diff marker、SEARCH/REPLACE marker。
   - 编译失败不进入 subprocess 执行。

3. submission 验证进入搜索闭环
   - 成功运行后必须检查 `submission.csv` 存在。
   - 必须检查行数、列名、列顺序、id 列。
   - 尽量调用 mlebench official format validation。
   - 无 submission 或格式错误的 attempt 不允许进入 best。

4. 每个 attempt 保存独立 submission
   - 使用 `submission_step_<id>.csv`。
   - best submission 单独复制保存。
   - 最终提交来自已验证 best submission，而不是最后一次 workspace 状态。

### P1：明显提高稳定性

1. 递归 data manifest
   - 文件树。
   - CSV shape 和 columns。
   - sample submission path 和 columns。
   - 关键 txt/json 文件前几行。

2. metric direction 和 metric 可信度
   - 预先确定指标方向。
   - lower-is-better 不能默认用 `>`。
   - stdout metric 必须和本地 validation 逻辑一致。

3. 错误 taxonomy
   - 分类：syntax、path、import、metric、format、timeout、content。
   - 同一 parent 同类错误超过 N 次停止扩展。
   - syntax/path 类错误强制进入 debug，不再普通 regenerate。

4. patch-based improvement
   - 对 valid parent 采用局部 patch 或 SEARCH/REPLACE。
   - patch apply 失败时重试。
   - 禁止把 diff 文本直接写进脚本。

### P2：后续优化

1. content quality check
   - 空提交。
   - 常量预测。
   - placeholder。
   - 行数/列数异常。

2. 环境 smoke test
   - 常见库 import 检查。
   - 对 `lightgbm`、`xgboost`、`sentencepiece`、`librosa` 等常见问题提前提示。

3. top candidates 管理
   - 保存 top-N valid submissions。
   - 按 branch 做 diversity 限制。
   - 定期复核 top candidates 的 official format。

---

## 9. 建议实施顺序

| 顺序 | 改动 | 预期收益 |
|------|------|----------|
| 1 | 严格 code extraction + compile gate | 直接消除大部分 SyntaxError 浪费 |
| 2 | submission format validation | 防止无效结果进入 best |
| 3 | attempt 独立 submission 保存 | 降低覆盖和 rerun 不一致风险 |
| 4 | recursive data manifest | 减少路径和格式猜测 |
| 5 | error taxonomy + repeated failure limit | 防止同类错误反复出现 |
| 6 | top candidate pool | 提高最终提交选择质量 |
| 7 | staged debug/improve | 提高长预算 exploitation 能力 |

最小可行修复是前 3 项。它们不需要重写搜索算法，但能立刻降低本轮暴露出的最大浪费。

---

## 10. 结论

MLEvolve 在 12 小时长跑中更高效，核心不是“更快执行”，而是“更少把无效候选当实验跑”。它通过编译门禁、格式验证、内容检查、节点隔离、分阶段 debug/improve、UCT/Top-K 切换和候选池管理，把搜索预算更多投入到有效分支上。

efficient-auto-research 的短跑优势仍然存在：它轻、快、开销低，适合快速 baseline。但当前长跑失败率过高，尤其是 SyntaxError、路径错误、submission 格式和自报 metric 可信度问题，导致 12 小时预算没有充分转化成有效实验。

下一步应先补齐 EAR 的执行和验证门禁，再考虑改搜索策略。优先修复 `code extraction -> compile gate -> submission validation -> candidate persistence` 这条链路，预计比直接改 KTS 更快见效。

---

## 11. 关联文件

关键代码：

```text
mle-bench-agents/efficient-auto-research/agent/engine/search.py
mle-bench-agents/efficient-auto-research/agent/engine/executor.py
mle-bench-agents/efficient-auto-research/agent/engine/thompson.py
baselines/MLEvolve/utils/response.py
baselines/MLEvolve/utils/data_preview.py
baselines/MLEvolve/agents/result_parse_agent.py
baselines/MLEvolve/engine/validation/format_server.py
baselines/MLEvolve/engine/validation/quality_check.py
baselines/MLEvolve/engine/solution_manager.py
```

研究文档：

```text
mle-bench-research/01_benchmark_and_selection.md
mle-bench-research/02_setup_and_agents.md
mle-bench-research/03_experiments_and_findings.md
mle-bench-research/04_mlevolve_vs_efficient_efficiency.md
```
