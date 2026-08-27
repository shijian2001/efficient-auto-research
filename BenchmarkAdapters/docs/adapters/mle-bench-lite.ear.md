# EAR × MLE-Bench Lite

| | |
|---|---|
| 形态 | 原生 Docker 启动器 |
| registry `mle_backend` | `native-docker` |
| 源码树 | `mle-bench-agents/efficient-auto-research` |
| variant | 原版 ID（无 variant） |
| 入口 | `MLEBenchLite/adapter.py::_docker_command` → `docker-eval/run_in_docker.sh efficient-auto-research` |

## 做法

Adapter 不进 EAR 的搜索。它构造一条 `run_in_docker.sh` 命令，把
competition、GPU、steps、timeout 传进去，剩下的由 EAR 自己的 graph search 决定。

关键环境变量（`_docker_command`）：

- `EAR_AGENT_DIR` = registry 里的 `install_path`
- `EAR_CLI_MODE=g3_legacy` — **固定为 G3 传统 CLI**
- `EAR_OUTPUT_DIR` / `MLE_RUN_ROOT` = 本格 `output_dir`
- `MLE_BENCH_DATA_ROOT`、`SEED`、`BENCHMARK_TASK_SPEC_SHA256`
- relay 相关：`MODEL`、`UPSTREAM_BASE_URL`、`LLM_UPSTREAM_PROXY`、
  `LLM_FORCE_PARAMETERS_JSON`、`LLM_UPSTREAM_TIMEOUT`、`LLM_MAX_RETRIES`

### 为什么钉 `g3_legacy`

G3 的 CLI 早于后来 EAR 迭代加的 run-id / seed / provenance / multi-root 参数。
与其给 G3 注入它不支持的 flag，不如让实验身份由**隔离的输出路径**和
**host 侧 launch manifest** 承载。`run_in_docker.sh` 里对应
`EAR_CLI_MODE=g3_legacy` 分支。

### 源码隔离

容器**不挂载** `EAR_AGENT_DIR` 本身。`run_in_docker.sh` 先要求 tracked 源码
clean，再用 `archive_tracked_source` 生成只含 tracked 文件的快照，以 `:ro`
挂到原路径上；输出目录单独 `:rw` 挂载。这样容器里看不到未提交文件、别的 worktree、
或历史 run。

### 容器内的隔离断言

启动命令前置一段 `EAR_ISOLATION_CHECKS`，任一条不满足就用专属退出码失败：

| 检查 | 退出码 |
|---|---|
| public `description.md` 可读 | 96 |
| `${MLE_BENCH_DATA_ROOT}/${COMP}/prepared/private` 不存在 | 97 |
| `/root/.cache/mle-bench/.../prepared/private` 不存在 | 98 |
| `baselines/MLEvolve` 不可见 | 99 |
| `mle-bench-research` 不可见 | 100 |
| `docker_runs` 里没有本格之外的历史 run | 101 |

第 99/100 条是这一格特有的：EAR 与 MLEvolve 同在一个仓库里，必须证明 EAR 跑的时候
看不到对家源码和研究报告。

## 产物

`artifact_path = output_dir/submission.csv`。`MleLiteAdapter.run` 在跑之前对
所有 `submission_roots` 做快照，跑完后要求这个文件：非空、指纹与运行前不同、
且哈希不等于 `sample_submission.csv`（含 CSV 规范化后的等价判定）。
不满足就报 "declared final submission is missing, stale, empty, or sample-equivalent"。

**注意**：EAR 的 `submission_roots` 除 `output_dir` 外还包含
`install_path/docker_runs/<run_tag>_<comp>`，因为 G3 CLI 会往那里写。

## Relay

`native-docker` 路径**不**由 `MleLiteAdapter.run` 起 `RelayProcess`——
relay 由 `run_in_docker.sh` 在 host 侧起（`_host_relay_environment`
传入端点），token log 落 `TOKEN_LOG_PATH`。这与 workspace/native-host 路径不同，
那两条是 adapter 自己开 `RelayProcess` + Unix socket。

## 已知偏离

- G3 不接受 seed flag；`SEED` 只到 `run_in_docker.sh`，EAR 进程内的随机性由
  `EAR_CLI_MODE` 分支决定。
- `EAR_CLI_MODE` 另有 `g3_legacy_knowledge` / `g3_legacy_mle_knowledge` 两档
  cold-start 变体，**本次 campaign 不用**（用了对无知识库的对照不公平）。
