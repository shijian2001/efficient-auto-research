# CLI 参考

Arbor 安装 `arbor` 命令（外加几个更底层的入口点）。本页是完整参考。

## 命令

| 命令 | 作用 |
| --- | --- |
| `arbor` | 不带子命令时，行为等同于 `arbor run`——在当前目录启动一段交互式会话。 |
| `arbor run` | 启动一段 AI 驱动的研究会话。 |
| `arbor setup` | 交互式向导，写入你的 provider、model 与 API key。 |
| `arbor config` | 检视与管理已存储的配置。 |
| `arbor doctor` | 诊断你的环境（PATH、Python、git、API key）。 |
| `arbor report` | 处理一次已完成运行的报告。 |
| `arbor idea-check` | 针对单个想法对照 alphaXiv 公共 API 做新颖性 / 先行工作审查。 |
| `arbor version` | 打印已安装的版本。 |

!!! tip
    不带子命令运行 `arbor`（或 `arbor --cwd .`）等价于 `arbor run`。

## `arbor run` { #arbor-run }

```bash
arbor run [INSTRUCTION] [OPTIONS]
```

`INSTRUCTION` 是一个可选的研究目标种子（例如 `"maximize dev score without changing eval or
data"`）。省略它则从接入对话开始。

### 默认流程

1. 与接入智能体打开一段交互式对话。阅读/讨论请求保持在有范围限制的只读讨论模式；只有优化或启动目标才进入运行规划模式。
2. 智能体确认目标项目目录。`--cwd` 是默认权限边界；外部路径必须由你明确点名，恢复旧对话后也要重新授权。
3. 智能体暂存并展示一份精确计划；你在后续消息中明确确认后，CLI 才启动同一份计划。显示问题时不会在后台继续执行工具。
4. 你确认终端里展示的完整研究契约。
5. 针对所选项目跑一次快速预检。
6. Coordinator 运行至完成并写出 `REPORT.md`。

### 选项

| 选项 | 说明 |
| --- | --- |
| `--cwd PATH` | 项目目录提示。除非用了 `--yes`，否则接入会核实/调整它。默认 `.`。 |
| `--config, -c PATH` | 项目 YAML 配置。默认取目标项目里的 `research_config.yaml` / `arbor.yaml` / `autoresearch.yaml`。 |
| `--max-cycles N` | 定稿前最多完成/跳过/失败多少个想法实验。 |
| `--max-turns N` | Coordinator ReAct 轮次的硬上限——一个成本/失控安全阀。 |
| `--intake-max-turns N` | 每条 intake 消息允许的最大内部 Agent 轮数（默认 `30`）。 |
| `--run-name NAME` | `.arbor/sessions/` 下的会话名。默认是时间戳。 |
| `--resume` | 在现有工作空间/会话里从检查点续跑一次被中断的运行。 |
| `--workspace-dir PATH` | 会话/产物目录覆盖。默认 `<target>/.arbor/sessions/<run_name>`。 |
| `--verbose, -v` | 显示更底层的 coordinator 日志。 |
| `--yes-cwd PATH` | 当 `--yes` 跳过接入时的目标项目目录。与 `--yes` 配合时必填。 |
| `--yes, -y` | 跳过接入对话，直接用 指令 + `--yes-cwd` 启动。 |
| `--no-dashboard-input` | 关闭终端实时输入；提示/review 闸门在超时后自动继续。 |
| `--followup / --no-followup` | 在 `REPORT.md` 之后，打开一个关于已完成运行的只读问答提示（默认开）。 |
| `--verbose-preflight` | 连成功的预检项也打印（默认只显示失败/警告）。 |
| `--webui-port N` | 只读浏览器监控端口。交互式运行默认在 `8765` 附近自动启动。 |
| `--no-webui` | 不启动只读浏览器监控。 |
| `--interaction-mode, --mode MODE` | 人在回路模式：`auto`、`direction`、`review`、`collaborative`。 |
| `--allow-non-base-branch` | 允许从当前非 `main` 分支启动。开发时有用，做基准时有风险。 |

### 示例

```bash
# 交互式：与接入对话，然后在当前目录运行
arbor run

# 给一个目标种子，仍走接入流程
arbor run "improve held-out accuracy"

# 无头：完全跳过对话
arbor run "maximize the competition metric" \
  --yes --yes-cwd /path/to/project \
  --config /path/to/project/research_config.yaml

# 每个想法运行前请求批准
arbor run --mode review

# 续跑一次被中断的会话
arbor run --resume --run-name my-study
```

## 交互式斜杠命令 { #interactive-slash-commands }

运行进行时，在终端仪表盘里输入这些命令。你输入 `/` 时会弹出一个简短菜单；`/help` 列出全部。

| 命令 | 动作 |
| --- | --- |
| `/help` | 显示所有仪表盘命令。 |
| `/ask <question>` | 向只读伴随智能体询问关于运行的问题。 |
| `/steer <message>` | 向研究智能体注入一条消息。 |
| `/mode ask\|research` | 设定普通输入的默认目标对象。 |
| `/status` | 打印运行状态。 |
| `/skill <name...>` | 请智能体加载指定的技能。 |
| `/tree` | 打印当前想法树快照。 |
| `/evidence` | 显示分数/基线证据。 |
| `/reply` | 展开/折叠完整的伴随回答（或按 ++tab++）。 |
| `/chart` | 切换实时进度图。 |
| `/branches` | 显示探索过的分支 ref。 |
| `/cost` | 打印 token 用量。 |
| `/pause` | 请智能体在当前步骤后暂停。 |
| `/resume` | 在 `/pause` 后恢复。 |
| `/report` | 显示会话/报告产物路径。 |
| `/abort`（或 `/quit`） | 中止运行。 |

## `arbor idea-check` { #arbor-idea-check }

无需启动完整运行，即可对单个研究想法快速做新颖性 / 先行工作审查。它会派发一个 SearchAgent，调研 [alphaXiv](https://www.alphaxiv.org) 公共文献，并打印一份轻量判定：该领域已有工作的总结、最接近的相关论文、新颖性评估（`novel` / `partial-overlap` / `prior-art-exists`），以及具体的重叠风险。

```bash
# Python ≥ 3.12 默认内置，无需额外安装。
arbor idea-check "用实体-关系草稿提升多跳问答"
arbor idea-check "在代码生成中对方案做树搜索" --json
```

零配置：无需搜索端点或 alphaXiv API 密钥——它直接查询 alphaXiv 的公共接口。复用你已有的 Arbor LLM 凭证（`arbor login` / API 密钥）。

| 选项 | 作用 |
| --- | --- |
| `--focus TEXT` | 可选的聚焦指令（如 `prefer arxiv 2024`）。 |
| `--model TEXT` | 覆盖本次审查使用的模型。 |
| `--json` | 输出原始 SearchAgent JSON，而非渲染后的 Markdown。 |
| `--cwd PATH` | 工作目录（默认：当前目录）。 |

同一后端也驱动 Coordinator 在运行中的新颖性审查——`search.builtin_backend` 与 `search.auto_search_on_add` 详见[配置](configuration.zh.md)。

## 其它入口点

供高级/底层使用，Arbor 还安装：

| 命令 | 用途 |
| --- | --- |
| `executor` | 直接运行单个 executor。 |
| `coordinator` | 直接运行 coordinator。 |
| `run-research` | 更底层的运行入口点。 |
| `review-research` | 复盘一次已完成的运行。 |

大多数用户只需要 `arbor`。
