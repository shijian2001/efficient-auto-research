# Codex × MLE-Bench Lite

| | |
|---|---|
| 形态 | 通用 workspace + CLI |
| registry `mle_backend` | `generic-mle-workspace` |
| 源码树 | `baselines/Codex`（无 nested `.git`，跟外层走） |
| variant | 原版 ID（无 variant） |
| 入口 | `MLEBenchLite/adapter.py::_workspace_command` → host `codex` CLI |

## 做法

Codex 是通用编码 CLI，不带 MLE 搜索引擎。这一格不给它套外层循环——
准备一个 workspace，把任务书交给 `codex exec` 一次，让它自己在这个目录里干活。

### workspace 构造（`prepare_workspace`）

`output_dir/workspace/` 下放：

| 文件 | 来源 |
|---|---|
| `description.md` | public task 的 `description.md`（copy） |
| `sample_submission.csv` | public 目录里找到的 sample（见下） |
| `AGENT_TASK.md` | 规范任务书 `task_specs/mle-bench-lite.md` + 当前 competition ID |
| `input/` | 空目录 |

sample 的查找顺序：精确名（`sample_submission.csv` / `sampleSubmission.csv` /
`sample_submission_null.csv`）→ 递归找含 submission/sample 的 `.csv` →
从含 submission/sample 的 `.zip` 里取第一个 csv。都找不到就报错。

workspace 已存在时默认拒绝，`--force` 才 `rmtree` 重建。

### CLI 调用

```
codex exec --ephemeral --skip-git-repo-check
           --dangerously-bypass-approvals-and-sandbox
           --model <model>
           -c model_provider="benchmark_relay"
           -c model_providers.benchmark_relay.name="Benchmark relay"
           -c model_providers.benchmark_relay.base_url="http://127.0.0.1:6200/v1"
           -c model_providers.benchmark_relay.wire_api="responses"
           -c model_providers.benchmark_relay.requires_openai_auth=true
           <instruction>
```

`instruction` 默认是 `task_specs/mle-bench-lite.md` 的正文，与 Claude Code
那一格**逐字节相同**。`--ephemeral` + `--skip-git-repo-check` 避免它把 workspace
当成 git 仓库或写持久 session。

`--dangerously-bypass-approvals-and-sandbox` 不是放开沙箱。这一格已经跑在
host 的 Bubblewrap jail 里，jail 内没有 bwrap，Codex 自己再起一层 sandbox
会全部失败（"bubblewrap is unavailable"）。这个 flag 是 Codex 文档里给
「外部已经沙箱化」的用法；外层 jail 仍然只给 workspace、public task 目录和
relay socket。

`wire_api="responses"` 是 Codex 特有的：它走 Responses API，不是 chat completions。
relay 默认按客户端协议原样透传（chat 走 chat，responses 走 responses）；
只有显式打开 `LLM_FORCE_CROSS_PROTOCOL` 才会跨协议改写。

### 沙箱（`_workspace_sandbox_argv`）

Bubblewrap，`--die-with-parent --new-session --unshare-all`。只读挂
`/usr /bin /lib /lib64 /etc/ssl /etc/hosts /etc/passwd /etc/group /sys`，
`--proc /proc --dev /dev --tmpfs /tmp`。

- CLI 可执行文件挂到 `/agent-bin/<name>`
- workspace 可写，public task 目录只读
- 网络经 `sandbox_runner.py` + `LLMRelay/forwarder.py` 转到 relay 的 Unix socket，
  `/etc/resolv.conf` 被替换成只有 `nameserver 10.0.2.3`
- `CODEX_HOME=/tmp/codex-home`，里面预置 `auth.json` = `{"OPENAI_API_KEY":"proxy"}`
  （0600），让 CLI 的鉴权检查通过而不需要真 key
- `HOME=/tmp/home`，`XDG_*` 全部指到 tmpfs

GPU：`nvidia-smi --query-gpu=uuid` 解析出 UUID 填 `CUDA_VISIBLE_DEVICES`，
用 UUID 而不是序号，避免容器内外编号错位。解析失败直接报错。

## 产物

`artifact_path = workspace_dir/submission.csv` —— **在 workspace 里**，
不是 `output_dir` 根。Codex 要自己把最终 submission 写到这个路径。
新鲜度 / 非空 / 非 sample 判定与其他格一致。

## Relay

`generic-mle-workspace` 走 `MleLiteAdapter.run` 里的 `RelayProcess`：
adapter 在临时目录建 `relay.sock`，起 relay 进程，把 socket 传进沙箱。
token log 落 `output_dir/token_usage.jsonl`，relay log 落 `output_dir/relay.log`。
这与 `native-docker` 三家（relay 在 `run_in_docker.sh` 里起）不同。

## 已知偏离

- `max_turns` 对 Codex **不生效**（只有 Claude Code 那一格用）。Codex 的预算由
  `timeout_seconds` 控制。
- 单次 `exec` 会话，没有 adapter 侧重试。Agent 自己决定迭代几轮。
