# ML-Master 2.0 × MLE-Bench Lite

| | |
|---|---|
| 形态 | 原生 host 启动器（bwrap） |
| registry `mle_backend` | `native-mle` |
| 源码树 | `baselines/EvoMaster` |
| variant | 原版 ID（无 variant） |
| 入口 | `MLEBenchLite/adapter.py::_ml_master_command` → `.venv/bin/python run.py --agent ml_master_2` |

> 目录名是 `EvoMaster`，registry key 是 `ml-master-2`，display name 是
> `ML-Master 2.0`。三个名字指同一棵树。

## 做法

跑官方 `run.py --agent ml_master_2` 的完整 workflow（Draft / Research / Improve
三阶段由上游自己编排，adapter 不拆不包）。

前置：`require_clean_upstream_source("ml-master-2")`。

### 命令

```
<install_path>/.venv/bin/python run.py
    --agent ml_master_2
    --config <generated per-run config>
    --task <public_dir>/description.md
    --run-dir <output_dir>
```

### 必须外部生成 config

`request.config_path is None` 时直接报错：

> ML-Master 2 requires a generated per-run config_path; the upstream example
> contains task-specific paths and model settings

上游的 example config 里写死了任务路径和模型设置，直接用会串格。
所以每格必须由调用方（campaign 层）生成一份，adapter 只负责校验文件存在
并传进去。相关生成逻辑见 `MLEBenchLite/ml_master_config_worker.py`。

**这是七格里唯一一个 adapter 不能自足、必须上游调用方先备料的格。**

### 沙箱

同 AiScientist 的 `_native_host_sandbox_argv`，但**不挂 docker socket** —— 
ML-Master 2.0 在 host 进程内直接跑，不自己起容器。

挂载：`baselines/EvoMaster` (ro)、public task 目录 (ro)、`output_dir` (rw)、
`BenchmarkAdapters/` + `.venv` + Python runtime roots (ro)。
`HOME` / `TMPDIR` / `XDG_*` 指到 `output_dir` 子目录。

## 产物：wrapper 定位

ML-Master 2.0 通过复制 `submission_<uid>.csv` 晋升最优解，最终落在
`<workspace_dir>/best_submission/submission.csv`，其中
`workspace_dir = output_dir/workspace`。

`native_wrappers.py ml-master-2` 在原生命令跑完后把它复制到
`output_dir/submission.csv`，同样用 `open("xb")` 拒绝覆盖，
且要求源文件是非空正规文件（非符号链接）。

`artifact_path = output_dir/submission.csv`。

## Relay

`native-mle`，`MleLiteAdapter.run` 起 `RelayProcess` + Unix socket。

## 本地补丁

`baselines/EvoMaster` HEAD `07a80da`，相对上游只有：

- `evomaster/utils/llm.py`：把 `reasoning_effort` 从 config 传到请求里
  （campaign 统一 high，不是给 ML-Master 单独加探索）。
- `playground/ml_master_2/core/utils/watch_dog.py`：超时从写死 86400s 改成读
  `ML_MASTER_RUN_TIMEOUT_SECONDS`，默认仍 86400。adapter 目前**不设**这个
  环境变量，所以默认行为与上游相同；12h protocol 是否注入它，跑前要再确认。
- `uv.lock` 是新增锁文件（+3438），不是改已有 pin。

## 与 AO 那一格的关系

这一格是**完全原生可用**的。ML-Master 2.0 被排除在 Terminal AO 之外，
原因是 AO 的任务形状（git revision + dev pass rate）与它 Kaggle 形状的
playground 不兼容，**不是**它能力不足。见
[terminal-bench-ao.ml-master-2.md](terminal-bench-ao.ml-master-2.md)。
