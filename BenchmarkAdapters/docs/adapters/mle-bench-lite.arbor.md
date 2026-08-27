# Arbor × MLE-Bench Lite

| | |
|---|---|
| 形态 | 原生 Docker 启动器 |
| registry `mle_backend` | `unsupported:official Arbor MLE has no host dev-evaluator extension` |
| 源码树（registry） | `baselines/Arbor` |
| 源码树（docker 实际执行） | `baselines/Arbor-longrun-patched` |
| variant | **必须** `arbor-benchmark-patched` |
| 入口 | `MLEBenchLite/adapter.py::_docker_command` → `docker-eval/run_in_docker.sh Arbor` |

## 为什么原版 ID 直接拒绝

`MleLiteAdapter.build_command` 里，`arbor` 且 `selected_variant(...) is None`
时抛 `UnsupportedAdapterError`：

> original Arbor MLE is unsupported without a host development evaluator;
> the patched MLE runtime requires arbor-benchmark-patched

官方 Arbor 的 MLE 路径需要一个 host 侧 dev evaluator 扩展，上游没有。
所以这一格**只能**用显式 variant 跑，不会被原版 ID 自动 fallback。
成绩单上必须写 `arbor-benchmark-patched`，不能写成原版 Arbor 的分。

## 两棵树

这一格是唯一一个 registry 路径和实际执行路径不同的：

- `registry.AGENTS["arbor"].install_path` = `baselines/Arbor`
  （版本身份、`formal_source_clean` 查这棵）
- `run_in_docker.sh` 的 `ARBOR_SOURCE_REPO` 默认 = `baselines/Arbor-longrun-patched`
  （12 小时长跑实际执行这棵）

`Arbor-longrun-patched` 在 `bd78027` 之后多 5 个 commit：long-run 隔离路径、
candidate state/输入隔离、shell 工具里 scrub controller state、
verification 命令保留 controller state、`mle_bench_lite` executor_timeout 减半到 2h。

**读成绩时要注意这个分叉。** 详细 HEAD 见 `../ON_DISK_AGENT_VERSIONS.md`。

## 做法

沿用 MLEvolve 的低侵入集成模型：MLE-Bench 本身不改，只提供 prepared 数据
和一个外部格式校验器。Agent 容器只看得见 public task 目录。

`ARBOR_SOURCE_SUBTREE` 默认 `.`，可指向子树。启动前的硬检查：

1. `ARBOR_SOURCE_REPO` 必须是 git 工作树，否则退出 2
2. `ARBOR_SOURCE_DIR` 必须存在，否则退出 2
3. `git status --porcelain -- <subtree>` 必须为空，否则退出 2

然后记 `ARBOR_SOURCE_COMMIT`，生成快照到
`cache/arbor-source-snapshots/<run_tag>_<comp>.XXXXXX`。

> Arbor 自己带 nested `.git`。`run_in_docker.sh` 显式冻结**外层 benchmark
> commit**，防止 adapter 源码被那个内层仓库遮蔽。

输出目录默认 `run-logs/${RUN_TAG}_Arbor_${COMP}_gpu${GPU_ID}`，
由 `ARBOR_OUTPUT_DIR` 传给容器。

## 产物

`artifact_path = output_dir/submission.csv`，判定规则与其他格一致
（非空 / 比运行前新 / 不等于 sample）。Arbor 自己的 hash-bound submission
recovery 和外部官方评分链路保留不动。

## Relay

`native-docker`，relay 在 host 由 `run_in_docker.sh` 起。

## 本地补丁里会影响搜索的部分

`Arbor-longrun-patched` 相对上游不只是环境适配。读这一格分数时必须写明：

- `src/plugins/mle_kaggle.yaml`：`mle_bench_lite.executor_timeout` 从 4h 改成
  2h（`time_budget` 仍是 24h）。12h campaign 下，4h 超时连续三次会把整格预算
  吃光、零提交；减半是为了还能再试。这改变了单次 executor 能跑多久，是行为
  变化，不是纯 bugfix。对 Arbor **不利**（单次训练更短），但避免整格交白卷。
- `src/core/agent.py`：扩了 premature-stop nudge（第一轮纯文本也 nudge；
  `will <动词>` 也算未完成），long-run 树还开了 `continue_until_budget`——
  没到预算就纯文本结束会被要求继续。这是搜索时长/坚持程度的变化，对 Arbor
  **可能有利**。
- `src/coordinator/orchestrator.py`：eval_contract 覆盖 tree meta，并把
  `test_semantics=artifact_verification_only` 写进 prompt（「artifact
  integrity only; official/test grading remains external」）。这是防作弊/
  防拿 test 当反馈，不是加强搜索。
- `src/mle/state_store.py`：只存 candidate 与 receipt 的绑定，**不排序、
  不改选优**。

成绩单必须写 `arbor-benchmark-patched`，并记下 executor_timeout=2h 和
continue-until-budget。
