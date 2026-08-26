# 快速上手

本指南带你从全新安装走到一次运行中的研究会话。

## 1. 配置一个 provider

运行一次交互式安装向导。它会把你的 provider、model 和 API key 写入用户配置，这样你就不必每次
运行都重复填写：

```bash
arbor setup
```

!!! tip "首次运行的捷径"
    如果你在配置任何东西之前就启动了一次运行，Arbor 会在交互式终端中检测到缺失的配置，并自动
    引导你走完 `arbor setup`。

更愿意手动来？那就改设环境变量：

=== "Anthropic"

    ```bash
    export ANTHROPIC_API_KEY=sk-ant-...
    ```

=== "OpenAI"

    ```bash
    export OPENAI_API_KEY=sk-...
    ```

=== "OpenAI 兼容（LiteLLM）"

    ```bash
    export OPENAI_API_KEY=...            # 你的网关 key
    export OPENAI_BASE_URL=https://your-gateway/v1
    ```

完整的 provider 矩阵见[配置](configuration.md)。

## 2. 启动一次会话

使用 Arbor 的方式，就是在你的项目目录里运行 `arbor`：

```bash
cd my_project
arbor
```

这会打开一段**接入对话（intake chat）**。你用自然语言描述目标；接入智能体读取你的代码与
README，确认指标与基线，帮你完善出一个计划，待双方达成一致后启动研究。之后你便留在同一个终端中
——观察进度，并用斜杠命令引导运行。

!!! tip "还没有项目？试试自带的示例"
    想要一次**不耗 API、不需 GPU、亚秒级**且能完整旁观的运行，可以用
    [`examples/algotune_knn`](https://github.com/RUC-NLPIR/Arbor/tree/main/examples/algotune_knn)
    任务——在匹配参考输出的前提下，让一个暴力 k-NN 求解器跑得更快。请在你的 Arbor
    仓库**之外**运行它，以免实验 worktree 影响该仓库：

    ```bash
    cp -r examples/algotune_knn /tmp/algotune_knn
    cd /tmp/algotune_knn
    git init -q && git add -A && git commit -qm baseline
    arbor
    ```

!!! tip "一开始就给出目标"
    你可以把目标作为第一个参数传入，并仍然走接入流程：

    ```bash
    arbor "maximize dev score without changing eval or data"
    ```

??? note "无人值守 / 脚本化运行（CI）"
    若要跳过接入对话——用于基准或 CI——用 `--yes` 跳过聊天，并显式指向项目：

    ```bash
    arbor run "improve held-out accuracy" \
      --yes \
      --yes-cwd /path/to/project \
      --config /path/to/project/research_config.yaml
    ```

    日常使用建议用上面的交互式 `arbor`。

## 3. 观察运行过程

一次运行进行时，你有三个视图：

- **终端仪表盘** —— 当前循环、想法树与成本的实时状态。
- **只读 Web 监控** —— 自动在浏览器中、靠近 `8765` 端口启动（用 `--no-webui` 关闭，用
  `--webui-port` 改端口）。
- **`REPORT.md`** —— 最终成稿，在运行结束时生成。

在仪表盘里你可以用 `/status`、`/tree`、`/evidence`、`/cost`、`/pause`、`/resume` 等斜杠命令
引导运行。见 [CLI 参考](cli.md#interactive-slash-commands)。

## 4. 阅读结果

运行完成后，Arbor 写出一个 `REPORT.md`，并打开一个可选的只读问答提示，让你就这次已完成的
研究进行追问（用 `--no-followup` 关闭）。所有产物——想法树、检查点、日志以及每个实验的分支——都位于
`<project>/.arbor/sessions/<run_name>/` 下。

## 接下来去哪

<div class="grid cards" markdown>

-   :material-book-open-variant: **准备一个基准**

    接好评测命令、保护好数据，让 Arbor 能安全迭代。

    [:octicons-arrow-right-24: 准备基准](preparing-a-benchmark.md)

-   :material-sitemap: **工作原理**

    Arbor 循环、想法树与留出纪律。

    [:octicons-arrow-right-24: 工作原理](how-it-works.md)

</div>
