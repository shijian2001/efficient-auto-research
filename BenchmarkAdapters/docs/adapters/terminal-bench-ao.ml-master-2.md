# ML-Master 2.0 × Terminal-Bench AO — 不参与

| | |
|---|---|
| 状态 | **排除在 AO 比较集合之外** |
| registry `terminal_ao_backend` | `unsupported:Kaggle-shaped playground workspace; best-solution promotion is decided by submission.csv artifacts` |
| launcher | `TerminalAO/launchers/ml_master_2.py` — fail-closed 存根 |

## 为什么排除

ML-Master 2.0 的 playground 写死了 Kaggle 形状的 workspace
（`best_submission` / `best_solution` / `submission` / `working`），
并通过复制 `submission_<uid>.csv` 晋升最优解：

- `baselines/EvoMaster/playground/ml_master_2/core/playground.py:107-113,212,300`

AO 不产生这类产物，所以它的 Draft / Research / Improve 三阶段 workflow 无法表达
AO 候选，除非重写那个 core。

**任务形状不匹配，不是能力不足。** 它在 MLE-Bench Lite 上原生跑满 22 题，见
[mle-bench-lite.ml-master-2.md](mle-bench-lite.ml-master-2.md)。

## 之前是什么样

曾经的实现是 benchmark 自己写的外层循环 + 一段 benchmark 作者写的三阶段 prompt
序列，包住 `evomaster.agent`。同样属于把 harness 行为归因给 Agent，因此删除。

## variant 也不覆盖 AO

`ml-master-autoresearch-variant` 存在，但它的 `benchmarks` 是
`("autoresearch-architecture", "optimizer-design", "fml-bench")` ——
**`terminal-bench-ao` 被有意排除**，`thin_registry.py` 里有注释说明。
所以不存在"用 variant 把它塞进 AO"的路径。

## 现在的 fail-closed 三层

与 MLEvolve 相同：`TerminalAOAdapter.__init__` → `build_native_ao_command`
→ launcher 存根，三层各自抛 `UnsupportedAdapterError`。

见 [terminal-bench-ao.mlevolve.md](terminal-bench-ao.mlevolve.md#对分母的影响)
关于 AO 分母是 5 不是 7。
