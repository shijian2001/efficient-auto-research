# AiScientist × MLE-Bench Lite

| | |
|---|---|
| 形态 | 原生 host 启动器（bwrap） |
| registry `mle_backend` | `native-mle` |
| 源码树 | `baselines/AiScientist` |
| variant | 原版 ID（无 variant） |
| 入口 | `MLEBenchLite/adapter.py::_ai_scientist_command` → `.venv/bin/aisci mle run` |

## 做法

跑官方 `aisci mle run` 全流程，adapter 不介入它的 workflow。与 Docker 三家的区别是
**它在 host 上跑**（外面套 bwrap），而不是由 `run_in_docker.sh` 起容器。
AiScientist 自己会用 Docker（`DOCKER_HOST=unix:///run/docker.sock` 透传进沙箱）。

前置：`require_clean_upstream_source("ai-scientist")` —— 这一格在 build 命令阶段
就要求 nested git 干净，不等到 formal preflight。

### 命令

```
aisci --output-root <output_dir> [--llm-profile-file <generated>]
      mle run --data-dir <public_dir>
              --llm-profile <profile_name>
              --gpu-ids <gpu_id>
              --time-limit <timeout>s
              --wait --skip-final-validation
      [--image <runtime_image>] [--pull-policy <policy>]
```

`--skip-final-validation` 是有意的：最终校验由 host 侧官方 grader 做，
不让 Agent 侧自己判定通过与否。

### 生成 LLM profile

AiScientist 用 YAML profile 描述后端，不读环境变量里的 base_url。所以 adapter
按 model-track 生成一份 profile 写到 `output_dir/ai-scientist-llm-profile.yaml`：

- `backends.benchmark-relay`：type `openai`，api_key / base_url 从环境变量取
- `profiles.<name>`：model、`api`（`responses` 或 `completions`）、
  `limits.max_completion_tokens`
- 可选：`limits.context_window`、`reasoning.effort`、`sampling.temperature`

`api_mode` 的判定：`model_parameters` 里 `use_completion_api is True` 或
`api_mode == "completions"` 时用 completions，否则 `responses`。

写入用 `open("x")` —— **文件已存在就报错**，不覆盖。
若调用方传了 `--official-llm-profile`，则跳过生成，直接用官方 profile 名。

### 沙箱（`_native_host_sandbox_argv`）

bwrap `--die-with-parent --new-session --unshare-all`。挂载：

| 路径 | 模式 |
|---|---|
| `baselines/AiScientist` | ro |
| public task 目录 | ro |
| `output_dir` | rw |
| `BenchmarkAdapters/` + `.venv` + Python runtime roots | ro |
| `/run/docker.sock` | 透传（这一格特有） |

`HOME`、`TMPDIR`、`XDG_CACHE_HOME`、`XDG_CONFIG_HOME` 全部指到 `output_dir` 下的
子目录，`PYTHONPATH` 指向 `install_path/src`。网络同样只通 relay Unix socket。

## 产物：wrapper 定位

这一格的 `submission.csv` 不在固定路径上——AiScientist 把结果写进
`output_dir/jobs/<job_id>/workspace/submission/submission.csv`，job id 运行时才生成。

所以命令外面套了 `MLEBenchLite/native_wrappers.py ai-scientist`：

1. 跑之前记下 `jobs/` 下已有的目录名
2. 跑原生命令
3. 跑完后 diff，**必须恰好多出一个新 job**，否则报错
4. 从那个 job 的 `workspace/submission/submission.csv` 复制到 `output_dir/submission.csv`
5. 复制用 `open("xb")` —— 目标已存在就报错，不覆盖

"恰好一个新 job" 这条是防止把上一次 run 的产物当成本次结果。

`artifact_path = output_dir/submission.csv`（wrapper 复制后的那份）。

## Relay

`native-mle` 走 `MleLiteAdapter.run` 的 `RelayProcess` + Unix socket
（与 workspace 两家相同，与 native-docker 三家不同）。
`OPENAI_API_KEY=proxy`，真实上游由 relay 持有。
