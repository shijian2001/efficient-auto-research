# ML-Master 2.0 × MLE-Bench Lite

| 项目 | 当前实现 |
|---|---|
| registry key / 目录 | `ml-master-2` / `baselines/EvoMaster` |
| 形态 | 原生 host workflow，外层 Bubblewrap + runtime / artifact wrapper |
| variant | `ml-master-2@<当前 pin>`，精确值见[版本记录](../ON_DISK_AGENT_VERSIONS.md) |
| 入口 | `adapter.py::_ml_master_command` → `native_wrappers.py` → `ml_master_runtime.py` → `run.py --agent ml_master_2` |
| 原生产物 | `<output_dir>/workspaces/task_0/best_submission/submission.csv` |
| 对外产物 | `<output_dir>/submission.csv`，交 host 官方 grader |

## 原生 workflow 与运行包装

Draft / Research / Improve 仍由 EvoMaster 编排；adapter 负责生成配置、隔离、预算、
执行兼容与发布产物。不是只抽取上游一段 prompt 代替完整 Agent。

`require_clean_upstream_source("ml-master-2")` 要求当前 pin 与干净源码。
`request.config_path` 必须由 campaign 层生成，不能直接使用带题目路径/模型设置的
上游 example。生成逻辑在 `ml_master_config_worker.py`，lower-is-better 由 host 的
官方 metric-direction worker 注入。

原生调用形状为：

```text
<locked-python> run.py --agent ml_master_2
    --config <generated-config>
    --task <public-description>
    --run-dir <output_dir>
```

单任务仍走上游 batch 入口，任务 ID 为 `task_0`，实际 workspace 是
`<output_dir>/workspaces/task_0`，不是旧文档里的 `<output_dir>/workspace`。

## 预算、监控和执行兼容

`ml_master_runtime.py` 在原生调用外维护截止时间与运行监控。adapter 设置
`ML_MASTER_RUN_TIMEOUT_SECONDS=request.timeout_seconds`；单个候选服从原生请求的
command timeout 和整格剩余时间，不再施加固定 90 分钟候选上限，保留 90 秒用于发布。

- PATH 优先使用 EvoMaster 锁定 venv，避免生成的 `python run.py` 落到没有 python 的宿主路径。
- TMPDIR 使用沙箱内的短 `/tmp`，避免 multiprocessing 的 Unix socket 路径过长；
  HOME 和 XDG 目录按运行隔离。
- BLAS/OpenMP 线程限制由 adapter 设置；当前 v7 不改生成代码自己的 DataLoader worker
  数，也不额外设置 `NUMPY_MADVISE_HUGEPAGE`。
- `ML_MASTER_CANDIDATE_POLICY=native`；监控不依据下载/错误日志自行中止候选。
  host 与 sandbox 的监控状态分开保存，训练和监控失败不能混为一谈。

源码在 8 月冻结之后又增加了 `evomaster/env/local.py` 的 execution helper hook，
并修正 Draft/Improve 可选检索的处理。完整源码身份在版本记录中维护，不能继续使用
旧 `07a80da` 作为当前运行 pin。

## 产物发布与评分

`native_wrappers.run_ml_master` 运行期间持续将上游当前 `best_submission` 原子同步到
`output_dir/submission.csv`，后续晋升会更新副本。正常结束再核对/发布最终文件；如果
研究循环后来失败，已存在的有效 best 文件仍可保留，是否有效和最终得分由 host grader
决定。包装器不从多个候选自行挑最优分，也不把“存在 CSV”当作有效成绩。

## 沙箱与 relay

`_native_host_sandbox_argv` 只读挂载源码、public task、adapter/runtime 等必要路径，
输出目录可写，不挂 Docker socket。ML-Master 在本地进程内执行候选，不自行创建容器。

`MleLiteAdapter.run` 启动 per-run relay 与 Unix socket。共享 host 服务地址来自
model-track，当前默认 6201；v7 使用各 slot 的配置，详见[运行手册](../CAMPAIGN_LAUNCH.md)。
Agent 使用本地代理凭据，provider key 留在 host。

## 与 AO 的关系

ML-Master 在 MLE 运行完整 workflow，但不参与 Terminal AO。AO 候选是 harness git
revision，而上游 workspace/晋升逻辑依赖 Kaggle CSV 产物，见
[Terminal AO 排除说明](terminal-bench-ao.ml-master-2.md)。
