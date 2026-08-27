# AiScientist × Terminal-Bench AO

| | |
|---|---|
| 形态 | AO native launcher（Python 进程内调用） |
| registry `terminal_ao_backend` | `unsupported:no official generic terminal entrypoint` |
| 源码树 | `baselines/AiScientist` |
| variant | **必须** `ai-scientist-terminal-variant` |
| launcher | `TerminalAO/launchers/ai_scientist.py` |
| runtime | `baselines/AiScientist/.venv/bin/python` |

## 为什么必须用 variant

官方 AiScientist 没有通用 terminal 入口。`build_native_ao_command` 里：

```python
if request.agent == "ai-scientist" and variant is None:
    raise AdapterError("unreachable original ai-scientist Terminal AO dispatch")
```

原版 ID 跑不出 AO 分，且不会自动 fallback 到 variant。成绩单上必须写
`ai-scientist-terminal-variant`。

## variant 是什么

`TerminalTaskSubagent`（`src/aisci_agent_runtime/subagents/terminal_task.py`），
是本地补丁加进 AiScientist 源码树的一个 subagent 类，走的是 AiScientist
自己的 subagent 框架（`SubagentConfig` / `SubagentStatus` / `ShellInterface`），
不是 adapter 另写的循环。搜索、工具调用、何时停都由这个 subagent 决定。

对应的本地 pin 现在是 `baselines/AiScientist` 的 `aae385b`
（`770039a` TerminalTaskSubagent + `61522b7` Dockerfile ARG +
`aae385b` MLE 域读 `AISCI_LLM_PROFILE_FILE`），见
`../ON_DISK_AGENT_VERSIONS.md`。`thin_registry.UPSTREAM_REVISIONS["ai-scientist"]`
必须与此一致。

## 做法

```python
subagent = TerminalTaskSubagent(
    shell=ShellInterface(working_dir=<candidate>),
    llm=create_llm_client(LLMConfig(provider="openai", api_mode="completions", ...)),
    config=SubagentConfig(max_steps=500, time_limit=<timeout>,
                          reminder_freq=20, log_dir=..., output_dir=...),
)
result = subagent.run(<instruction>)
```

`api_mode="completions"` 是这一格特有的——AiScientist 的 LLM client 走
chat completions，不是 responses。model-track 里的 `max_tokens` /
`temperature` / `reasoning_effort` / `context_window` / `request_timeout` /
`retry_budget` 逐项映射进 `LLMConfig`。

### instruction

与 EAR / Codex / Claude 那三格**不同**：这一格的 prompt 是 launcher 里写死的
一段话，而不是 `task_spec_text("terminal-bench-ao")` 原文。内容包含：
优化 terminus-2、只改允许路径、用给定 DEV 命令评估候选、不要探测隐藏 test
身份、最后把最好的候选留在原地。

> **这是一处已知的框架不对称。** 另外三家收到的是逐字节相同的规范任务书；
> 这一格是改写过的。跨格比较时应记入偏离表。

### 结束状态

`COMPLETED` 或 `TIMEOUT` 视为正常；其他状态抛错。结果写
`native-result.json`（`write_json_exclusive`，不覆盖），记录
`native_loop` = `aisci_agent_runtime.subagents.terminal_task.TerminalTaskSubagent.run`、
status、num_steps、runtime_seconds、token_usage、log_path。

## seed

`PYTHONHASHSEED` 设为 seed，但 `run_native_loop` 本身**不接 seed 参数** ——
TerminalTaskSubagent 没有显式 seed 接口。这一格的可复现性弱于 EAR 那一格。

## 共享部分

candidate / dev broker / sealed test / 沙箱见 `TerminalAO/supervisor.py`
与 `../IMPLEMENTATION.md`。
