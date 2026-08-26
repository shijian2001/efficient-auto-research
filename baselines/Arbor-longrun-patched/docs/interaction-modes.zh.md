# 交互模式（人在回路）

Arbor 默认完全自主运行，但由你决定想要多少监督。**交互模式**控制智能体何时——若有的话——
暂停来征询你；另有一组实时控制，让你在运行进行时引导它。

## 四种模式

| 模式 | 行为 |
| --- | --- |
| `auto` | 完全自主。智能体从不为输入而暂停。 |
| `direction` | 智能体在关键节点询问**往哪探索**。 |
| `review` | 智能体在运行想法前请你**批准或修改**它们。 |
| `collaborative` | `direction` 与 `review` 两道闸门都启用。 |

### 设定模式

按次运行，在命令行上（别名 `--mode`）：

```bash
arbor --mode review
arbor --interaction-mode collaborative
```

或在配置中持久设定：

```yaml title="research_config.yaml"
ui:
  interaction_mode: auto      # auto | direction | review | collaborative
```

一如既往，CLI 参数会覆盖配置值。见
[配置 → 当两者冲突时：优先级](configuration.md#when-settings-disagree-precedence)。

## 一道闸门是什么样

当一道闸门触发时，智能体会暂停并征询你的输入——在终端仪表盘里，以及对交互式运行而言，在
[Web UI](web-ui.md) 里。例如在 `review` 模式下，你可以原样批准一个被提出的想法、编辑它，或
改变方向；在 `direction` 模式下，你指示下一步该扩展树的哪一部分。

!!! tip "无人值守的运行仍会推进"
    传入 `--no-dashboard-input`，闸门会在**超时后自动继续**，而不是永远阻塞。这让你能无人值守地
    跑一个 `review`/`collaborative` 研究：它会短暂暂停等待输入，若你不在便自行继续。

## 实时引导一次运行

无论何种模式，你总能从终端仪表盘（以及交互式 Web UI）用[斜杠命令](cli.md#interactive-slash-commands)
影响一次进行中的运行：

| 命令 | 用途 |
| --- | --- |
| `/steer <message>` | 直接向研究智能体注入引导。 |
| `/ask <question>` | 向只读伴随智能体询问运行情况（不改变它）。 |
| `/skill <name...>` | 请智能体按需加载一个 [Skill](skills.md)。 |
| `/pause` / `/resume` | 在当前步骤后暂停，然后恢复。 |
| `/tree`、`/evidence`、`/branches` | 在决定如何引导前检视状态。 |
| `/abort` | 停止运行。 |

## 选择一种模式

| 你想要…… | 用 |
| --- | --- |
| 最大自主 / 基准 | `auto` |
| 让智能体保持在你关注的研究方向上 | `direction` |
| 在花费算力前对每个假设做一次把关 | `review` |
| 在难题上紧密协作 | `collaborative` |
| 放手运行但保留轻度监督 | 任意模式 + `--no-dashboard-input` |

关于闸门如何嵌入搜索循环，见
[工作原理 → 人在回路](how-it-works.md#human-in-the-loop)。
