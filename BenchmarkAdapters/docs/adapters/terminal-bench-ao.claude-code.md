# Claude Code × Terminal-Bench AO

| | |
|---|---|
| 形态 | AO native launcher（CLI 子进程） |
| registry `terminal_ao_backend` | `native-claude-cli` |
| 源码树 | `baselines/ClaudeCode`（无 nested `.git`） |
| variant | 原版 ID（无 variant） |
| launcher | `TerminalAO/launchers/common.py::_claude` |

## 做法

与 Codex 那一格同构：candidate 目录里起一次 CLI，任务书 + DEV 命令交出去，
自己迭代，没有 adapter 侧外层循环。

```
claude --print --no-session-persistence
       --model <model>
       --permission-mode bypassPermissions
       <instruction>
```

`cwd` = candidate 目录，`timeout_seconds` = 剩余预算。

### 与 MLE 那一格的差别

| flag | MLE 格 | AO 格 |
|---|---|---|
| `--print` | ✓ | ✓ |
| `--bare` | ✗ | ✗ |
| `--no-session-persistence` | ✗ | ✓ |
| `--permission-mode bypassPermissions` | ✓ | ✓ |
| `--max-turns` | ✓（campaign 传 1000） | **✗** |

AO 这一格**不传** `--max-turns`，预算纯由 wall clock 控制 ——
这样和 Codex 对称。MLE 那一格传了，所以那边两家不对称。
AO 仍传 `--no-session-persistence`；MLE 那一格刻意不传，好留下 Claude
自己的 session 记录。

### instruction

`task_spec_text("terminal-bench-ao")` + `"\n\nDEV capability: " + dev_command`，
与 Codex 逐字节相同。

## Agent 看得见什么

同 Codex：正式模式外套 bwrap `--unshare-all`，只有 candidate、locked runtime、
`evaluate-dev` socket、relay socket。见
[terminal-bench-ao.codex.md](terminal-bench-ao.codex.md#agent-看得见什么)。

## 产物

Agent 留在 trunk 上的 revision，`selection_policy = "agent-declared"`。
