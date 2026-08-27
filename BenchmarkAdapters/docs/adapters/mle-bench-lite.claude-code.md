# Claude Code × MLE-Bench Lite

| | |
|---|---|
| 形态 | 通用 workspace + CLI |
| registry `mle_backend` | `generic-mle-workspace` |
| 源码树 | `baselines/ClaudeCode`（无 nested `.git`，跟外层走） |
| variant | 原版 ID（无 variant） |
| 入口 | `MLEBenchLite/adapter.py::_workspace_command` → host `claude` CLI |

## 做法

与 Codex 那一格**共用同一条代码路径**（`_workspace_command`），
workspace 构造、沙箱、GPU 解析、产物判定全部相同。只有 CLI 参数不同。

workspace 内容见 [mle-bench-lite.codex.md](mle-bench-lite.codex.md#workspace-构造prepare_workspace)，
不在此重复。

### CLI 调用

```
claude --print --bare --no-session-persistence
       --model <model>
       --permission-mode bypassPermissions
       --max-turns <max_turns>
       <instruction>
```

`instruction` 与 Codex 逐字节相同（`task_specs/mle-bench-lite.md`）。

各 flag 的用意：

| flag | 为什么 |
|---|---|
| `--print` | 非交互，一次性输出 |
| `--bare` | 去掉终端装饰，日志可解析 |
| `--no-session-persistence` | 不写 session 文件，格与格之间不串 |
| `--permission-mode bypassPermissions` | 沙箱已经限制了能力边界，再弹权限确认会卡死非交互进程 |
| `--max-turns` | **这一格特有的预算参数**，`MleLiteRequest.max_turns` 默认 8 |

`--max-turns` 是 Codex 那一格没有的。两家的预算口径因此不完全对称：
Claude Code 受 turn 数和 wall clock 双重约束，Codex 只受 wall clock 约束。
**跨格比较 token / 步数时要记得这一点。**

### 沙箱

同 Codex：bwrap `--unshare-all`，网络只通 relay 的 Unix socket，
`HOME=/tmp/home`，`ANTHROPIC_API_KEY=proxy`。

注意沙箱环境里 `CODEX_HOME=/tmp/codex-home` 是无条件设的（共用代码路径），
对 Claude Code 无害但也无用。

## 产物

`artifact_path = workspace_dir/submission.csv`。判定规则同其他格。

## Relay

同 Codex：`MleLiteAdapter.run` 起 `RelayProcess` + Unix socket，
`base_url=http://127.0.0.1:6200/v1`，`ANTHROPIC_API_KEY=proxy`。
