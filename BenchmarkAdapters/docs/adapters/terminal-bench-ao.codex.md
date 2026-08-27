# Codex × Terminal-Bench AO

| | |
|---|---|
| 形态 | AO native launcher（CLI 子进程） |
| registry `terminal_ao_backend` | `native-codex-cli` |
| 源码树 | `baselines/Codex`（无 nested `.git`） |
| variant | 原版 ID（无 variant） |
| launcher | `TerminalAO/launchers/common.py::_codex` |

## 做法

在 candidate 目录里起一次 `codex exec`，把任务书 + DEV 能力交给它，
让它自己迭代。没有 adapter 侧的外层循环。

```
codex exec --ephemeral --skip-git-repo-check
           --dangerously-bypass-approvals-and-sandbox
           --model <model>
           -c model_provider="benchmark_relay"
           -c model_providers.benchmark_relay.name="Benchmark relay"
           -c model_providers.benchmark_relay.base_url="<relay>"
           -c model_providers.benchmark_relay.wire_api="responses"
           -c model_providers.benchmark_relay.requires_openai_auth=true
           <instruction>
```

`--dangerously-bypass-approvals-and-sandbox` 的理由与 MLE 那一格相同：
正式 AO 会再套一层 host Bubblewrap jail，jail 里没有 bwrap，Codex 自己的
sandbox 起不来。外层 jail 仍然只给 candidate、locked runtime、dev socket
和 relay socket。

`cwd` = candidate 目录，`timeout_seconds` = supervisor 传入的剩余预算。

### instruction

```
task_spec_text("terminal-bench-ao")
+ "\n\nDEV capability: " + <dev_command>
```

`dev_command` 是一条可直接执行的命令行（`shlex.join`）：

```
<adapter .venv>/bin/python <dev_client.py> --socket <broker.sock> --token <token>
```

Codex 自己决定什么时候调它、调几次。`dev_client.py` 只支持一个 operation：
`evaluate-dev`。**没有任何 test 能力存在于搜索期间。**

与 Claude Code 那一格收到的 instruction 逐字节相同。

## Agent 看得见什么

正式模式下 launcher 命令还会再套一层 `sandbox_native_ao_command`
（bwrap `--unshare-all`）。进程只拿到：

1. 从 baseline materialize 出来的可写 candidate
2. 自己的 locked runtime
3. 只支持 `evaluate-dev` 的 Unix socket
4. host-owned LLM relay 的 Unix socket

**拿不到**：protocol / split 文件、89-task dataset、test 任务 ID、
test evaluator、Harbor 凭据、主仓库。

## 产物

Codex 把改动留在 candidate 目录的 trunk 上。Supervisor 在 launcher 退出后
调 `broker.declare_current()`，replay **Agent 自己声明的**那个 revision，
不是 host 从历史候选里挑分最高的。`selection_policy = "agent-declared"`，
`harness_selected_among_candidates = false`。

## 已知偏离

- `--max-turns` 不适用（Codex 无此 flag）；预算只由 wall clock 控制。
  Claude Code 那一格同样没传 `--max-turns`，所以 **AO 上这两家的预算口径是对称的**
  （与 MLE 那两格不同）。
