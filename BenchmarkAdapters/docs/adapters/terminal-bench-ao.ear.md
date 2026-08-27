# EAR × Terminal-Bench AO

| | |
|---|---|
| 形态 | AO native launcher（Python 进程内调用） |
| registry `terminal_ao_backend` | `native-ear-repository` |
| 源码树 | `mle-bench-agents/efficient-auto-research` |
| variant | 原版 ID（无 variant） |
| launcher | `TerminalAO/launchers/ear.py` |
| runtime | `BenchmarkAdapters/environments/mle/ear/.venv/bin/python` |

## 做法

调 EAR 自己的 repository 模式 `agent.run_repo.run_repo_search`，
由它内部的 Kernel Thompson Sampling 循环决定试什么、什么时候停。

Launcher 只做两件 EAR 不该知道的事：

1. 告诉它这个 benchmark 声明哪些路径可编辑
2. 把 host-owned DEV evaluator 包成一个普通 callable 注入进去

**EAR 从头到尾不知道 Harbor 存在。**

### 注入的 evaluator

```python
def evaluate(workspace) -> EvaluationResult:
    payload = evaluate_dev(workspace, dev_command)
    return EvaluationResult(score=float(payload["pass_rate"]), feedback=payload)
```

`evaluate_dev` 做 adapter 侧的完整性检查（响应带齐必需字段、36 题分母正确），
EAR 只拿到一个 score 加不透明 payload。`metric_sign=1`（dev pass rate 越高越好）。

### 任务文本

`task_spec_text("terminal-bench-ao")` —— 与 Codex / Claude Code 收到的
**逐字节相同**。没有哪家拿到别家没有的任务框架。

### 完整性断言

跑完后检查返回 payload：

```python
if payload.get("native_selection") != "agent.engine.thompson.select_parent":
    raise RuntimeError("EAR did not run its native Kernel Thompson Sampling loop")
```

这是这个 launcher 存在的全部意义：如果这条不成立，说明 EAR 走了某条 fallback
路径而不是自己的控制循环，那这个结果就不是 EAR 的结果。

结果写 `<launcher_output_dir>/native-result.json`，
用 `write_json_exclusive` —— 已存在就拒绝，不覆盖。

## 参数

`max_steps=50`（launcher 默认），`timeout` 由 supervisor 传入剩余预算，
`temperature` 从 model-track 取，`seed` 同时设进 `EAR_SEED` 环境变量。

## 可编辑路径

```
terminus_2.py
terminus_json_plain_parser.py
terminus_xml_plain_parser.py
tmux_session.py
templates/
```

其余 baseline 文件由 supervisor 算成 `protected_paths` 传给 launcher。

## 共享部分

candidate 准备、dev broker、sealed test、沙箱、评分见 `../IMPLEMENTATION.md`
的 "Terminal AO Fairness Boundary" 与 `TerminalAO/supervisor.py`。
