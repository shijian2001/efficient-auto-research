# Arbor × Terminal-Bench AO

| | |
|---|---|
| 形态 | AO native launcher（官方 CLI + plugin） |
| registry `terminal_ao_backend` | `official-extension-thin-arbor-cli` |
| 源码树 | `baselines/Arbor` |
| variant | 可选 `arbor-benchmark-patched`（与 MLE 那格不同，AO 原版 ID **可用**） |
| launcher | `TerminalAO/launchers/common.py::_arbor` |

## 做法

这一格是五格里最"上游原生"的：用官方 `arbor run` CLI，通过 Arbor 自己的
**plugin evaluator contract** 注入 DEV 评估器。Arbor 的 tree / merge /
promotion / selection / stop 行为完全不动。

```
arbor run <instruction>
      --cwd <candidate>
      --yes --yes-cwd <candidate>
      --workspace-dir <launcher_output>/arbor-session
      --config <generated arbor-thin-config.yaml>
      --interaction-mode auto
      --no-followup --no-webui
```

原版 ID 时额外要求 `require_clean_upstream_source("arbor")`。

### 生成的 config

`arbor_thin.write_arbor_config` 写两个文件：

**`arbor-thin-config.yaml`** — `llm` 段：
- `provider`：`openai-responses`，或 model-track 里 `use_completion_api=True` /
  `api_mode="completions"` 时用 `openai-chat`
- `model`、`base_url`、可选 `reasoning_effort`、`max_tokens`

**`plugins/benchmark_dev.yaml`** — evaluator 契约：
- `metric_direction: maximize`（dev pass rate 越高越好）
- `eval_cmd`：调 `dev_client.py --operation evaluate-dev --candidate-root {cwd}`
  加上每个 `--editable <path>`，末尾 `--token "$TERMINAL_AO_DEV_TOKEN"`
- `protected_paths` / `required_outputs`

`{cwd}` 是 Arbor 的占位符 —— 它自己的 executor 会在每个 worktree 里展开。
这就是"官方 plugin 把 host-owned B_dev evaluator 注入每个 executor worktree"的实现。

### instruction

**与其他四格不同**，用 `_arbor_instruction()`：

```
task_spec_text("terminal-bench-ao")
+ 说明官方 project plugin 会把 host-owned B_dev evaluator 注入每个 executor
  worktree；held-out 评估不可用；用 Arbor 自己的 tree/merge/promotion/
  selection/stop 行为；把 Agent 选定的最终候选留在 trunk 上。
```

DEV 能力不是命令行文本（Codex/Claude 那样），而是 plugin —— 所以任务书必须
换一种说法告诉它评估器在哪。**这是一处已知的框架不对称。**

### 沙箱下的 socket 路径

`sandboxed=True` 时 socket 路径写成 `/capability/dev.sock`（沙箱内视角），
否则用 host 真实路径。eval 命令里用 `/usr/bin/python3` 而非 adapter venv 的
python，因为它要在 Arbor 的 executor worktree 里执行。

环境：`PYTHONPATH` 指向 `baselines/Arbor/src`，`SEED`，`TERMINAL_AO_DEV_TOKEN`。

## variant 的差别

`arbor-benchmark-patched` 与原版 ID 在这一格的**唯一**区别：

| | 原版 ID | patched variant |
|---|---|---|
| `require_clean_upstream_source` | ✓ | ✗ |
| `--max-cycles 24 --max-turns 96` | ✗ | ✓ |
| label | "Arbor native coordinator Terminal AO loop" | "Arbor benchmark-patched Terminal AO loop" |

命令其余部分完全相同。注意 patched 走的是**有预算上限**的路径。

> MLE 那一格与此相反：原版 ID 直接被拒，必须用 patched。
> 见 [mle-bench-lite.arbor.md](mle-bench-lite.arbor.md)。

## 产物

Arbor 自己 promote 到 trunk 的候选。`selection_policy = "agent-declared"`。
