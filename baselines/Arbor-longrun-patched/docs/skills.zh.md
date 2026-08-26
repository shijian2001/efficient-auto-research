# 技能

技能是 markdown **手册**，由智能体在相关时刻按需加载。[插件](plugins.md)声明*优化什么*，
而技能塑造智能体在某个特定步骤*如何思考*——比如怎样起草研究想法，或怎样从第一性原理审视一个问题。

!!! abstract "一句话区分技能与插件"
    **技能**调整智能体在某一步*如何推理*（一份在相关时加载的 markdown 清单）。[插件](plugins.md)
    描述一个完整领域*优化什么*。它们可组合——单用技能、单用插件，或两者并用。

!!! question "我需要写一个吗？"
    **不需要。** Arbor 随附了会自动加载的、合理的默认技能。只有当你想改进智能体在某个特定步骤的
    推理时，才写你自己的。

## 为什么要技能

LLM 驱动的研究有一些可预测的失败模式：跳过思考、直接转向看似合理的微调；凭记忆重建上下文而不去
读取状态；提议调参数而非真正的机制。一个技能是一份浓缩的指导，专门针对某一种失败模式——在它
关键的时刻精确注入，而不是埋在一个庞大的系统提示里。

## 技能格式

一个技能是带 YAML front matter 的 markdown 文件，外加指令本身：

```markdown
---
name: idea_drafting
description: Structured idea-drafting workflow for IDEATE rounds.
when_to_apply: At the start of every IDEATE round, BEFORE drafting any candidate idea.
---

# SKILL: Idea Drafting

You are about to enter IDEATE. Read this once now. Apply every part before
you propose a single candidate...
```

| 字段 | 用途 |
| --- | --- |
| `name` | 用于注册和引用该技能的标识符。 |
| `description` | 关于技能做什么的一行摘要。 |
| `when_to_apply` | 触发条件——智能体何时应加载并遵循它。 |
| *body* | 智能体遵循的实际手册。 |

## 随附的技能

Arbor 开箱随附一小套自动加载的技能：

| 技能 | 何时适用 |
| --- | --- |
| `idea_drafting` | 在每一轮 IDEATE 开始时，于起草候选想法之前。强制“机制，而非参数”的底线——真正的研究方向优先于参数微调。 |
| `first_principles_probe` | 当智能体应从第一性原理推理一个问题，而非套用熟悉的解时。 |

你可以从接入对话里为单次运行调整哪些技能处于激活状态——输入 `/` 用斜杠命令：

```text
/skill load my_skill            # 本次运行加载一个你自己的技能
/skill unload first_principles_probe   # 本次运行丢弃一个默认技能
/skill reset                    # 恢复默认
```

## 编写你自己的技能

1. 在你项目里建文件夹 `.arbor/skills/` 并在其中加一个 markdown 文件，例如
   `<project>/.arbor/skills/my_skill.md`。Arbor 从这个文件夹发现项目技能；同名 `name`
   的项目技能会覆盖随附的同名技能。
2. 加上 `name`、`description` 与 `when_to_apply` front matter。
3. 写手册。要具体、要有明确的规定性——当一个技能给出可执行的清单（而非含糊的鼓励）时最有效。

在对话里用 `/skill load my_skill` 加载它（或依赖 `when_to_apply` 自动触发它）。

!!! tip "技能 vs. 插件"
    用**插件**来定义一个领域的评测契约、保护路径与预算。用**技能**来改进智能体在某个特定
    步骤的推理。它们可组合：一个领域插件可以和那些为该领域优化构思的技能搭配。
