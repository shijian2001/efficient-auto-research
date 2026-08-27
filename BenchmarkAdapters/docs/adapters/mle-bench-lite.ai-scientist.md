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

## 运行镜像：上游没发布，本地构建

AiScientist 把 Agent 写的代码放进 Docker 容器执行（`src/aisci_runtime_docker/`），
所以可用镜像是它运行时的一部分，不是可选项。

上游开源了代码（MIT，163 个 Python 文件），但**没有发布可用镜像**：

- `config/image_profiles.yaml` 的 `mle-default` 指向
  `hub.byted.org/your-team/aisci-mle:latest` —— 内网 registry，`your-team`
  还是个占位符，本机连 DNS 都解析不了。
- `docker/mle-agent.Dockerfile` 的 `FROM` 也在同一内网。
- 它自带的 `docker/build_mle_image.sh` 本来是为此准备的：脚本会传
  `--build-arg BASE_IMAGE`，默认值就是公网的 `ubuntu:24.04`。但 Dockerfile
  **没有声明这个 ARG**，参数因此失效，构建仍然去拉内网镜像。

这不是版本旧，是上游发布时把外部可用的那一环漏掉了。表现为该格在 preflight
阶段就死：

```
Runtime image hub.byted.org/your-team/aisci-mle:latest is missing locally
and this run would pull it
```

处理方式是补上上游漏掉的那一行（本地 pin，不回上游），然后用**它自己的**
构建脚本产出镜像：

```bash
cd baselines/AiScientist && bash docker/build_mle_image.sh   # -> aisci-mle:test
```

镜像身份记在 `registry.AGENT_RUNTIME_IMAGES`，由 `campaign.py` 传进
`MleLiteRequest.runtime_image`。此前只有 `adapter_smoke.py` 会设这个字段，
正式路径无法覆盖那个不可达的默认值。

`pull_policy=never` 是有意的：正式 run 不允许静默联网拉镜像，镜像不在就直接
失败，而不是拉进来一个没记录的东西。

> **成绩单必须注明**：这一格的运行环境是本机按上游 docker/ 资产构建的，
> 不是上游发布的镜像——因为上游没有发布版可对照。

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

## 本地补丁（MLE 相关）

`baselines/AiScientist` HEAD 是 `aae385b`，不是文档里曾经写的 `770039a`：

- `61522b7`：Dockerfile 声明 `ARG BASE_IMAGE`，让上游自己的
  `build_mle_image.sh` 在公网能构建。环境适配。
- `aae385b`：MLE 域尊重 `AISCI_LLM_PROFILE_FILE`，否则 `--llm-profile-file`
  被静默忽略、格子起不来。环境适配。
- `770039a` 的 `TerminalTaskSubagent` 只给 AO 用，MLE 路径不走它。
- `config/llm_profiles.yaml` 多了一个 `gpt-5.5` 段；正式 campaign 用生成的
  `ai-scientist-llm-profile.yaml`（gpt-5.6-terra），不靠这份仓库内 profile。
