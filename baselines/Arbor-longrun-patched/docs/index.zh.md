---
hide:
  - navigation
---

# Arbor

*面向通用自主科研的假设树精炼方法（Toward Generalist Autonomous Research via Hypothesis-Tree Refinement）*

**Arbor 是一个自主科研智能体，它把一个长周期目标转化为持续累积的搜索过程。** 给它一个基准
（benchmark）和一个目标；它会提出假设、修改代码、运行真实实验、从结果中学习，并保留那些在
留出（held-out）数据上确实站得住脚的改进。

不同于“一次性尝试、过后即弃”的做法，Arbor 会生长出一棵**假设树**：每个想法都是一根分支——
失败则剪枝，成功则保留——而洞见会沿树反向传播，让后续想法从更可靠的起点出发。

<div class="grid cards" markdown>

-   :material-rocket-launch: **几分钟内跑起来**

    `pip install arbor-agent`、`arbor setup`，然后 `arbor`。

    [:octicons-arrow-right-24: 安装](installation.md)

-   :material-flask: **运行你的第一个研究**

    把 Arbor 指向一个基准，看着想法树生长。

    [:octicons-arrow-right-24: 快速上手](quickstart.md)

-   :material-sitemap: **理解方法**

    Arbor 循环、想法树、git 隔离与留出纪律。

    [:octicons-arrow-right-24: 工作原理](how-it-works.md)

-   :material-tune: **配置一切**

    Provider、预算、超时与人在回路模式。

    [:octicons-arrow-right-24: 配置](configuration.md)

</div>

## 两个协作的智能体

| 智能体 | 角色 |
| --- | --- |
| **Coordinator（协调者）** | 科研总监。维护想法树，通过 *arbor cycle* 驱动搜索，并派发实验。 |
| **Executor（执行者）** | 科研工程师。给定一个想法，它实现代码改动，在隔离的 git worktree 中运行实验，并汇报证据。 |

## 为什么是 Arbor

- **沉淀证据，而非日志。** 结果、失败模式与提炼出的洞见保存在持久化的想法树中——不是滚动缓冲区。
- **默认的留出纪律。** Executor 在 dev 划分上迭代；只有在留出 test 划分上跨过可配置阈值的改进
  才会被合并。
- **隔离、可回退的实验。** 每个实验都在自己的 git worktree、独立分支上运行。在你主动合并之前，
  `main` 绝不会被触碰。
- **反向传播的洞见。** 每次实验后，由 LLM 抽象出学到的东西并上推到树中，让同辈与后代想法继承
  来之不易的上下文。
- **任意模型。** Anthropic、OpenAI / Responses API，或任何通过 LiteLLM 接入的 OpenAI 兼容后端
  （DeepSeek、Gemini、Qwen、vLLM、Ollama、本地网关）。
- **无需改代码的领域适配。** 一行 `plugin:` 即可把智能体切换到新领域；Skill 则是按需加载的
  markdown 操作手册。

!!! tip "第一次使用？"
    先看[安装](installation.md) →[快速上手](quickstart.md)，再读[工作原理](how-it-works.md)
    了解各个组成部分。
