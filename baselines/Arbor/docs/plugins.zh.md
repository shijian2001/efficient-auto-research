# 插件

插件无需改动任何代码，就能把 Arbor 重定向到一个**领域**。它是单个 YAML 文件，声明了如何评估工作、
什么必须保持受保护、需要哪些产出、一份算力预算，以及给智能体的一些领域指导。

!!! abstract "一句话区分插件与技能"
    **插件**描述*优化什么*——一个完整领域的评测规则、保护文件与预算。[技能](skills.md)磨炼智能体
    在某一步*如何推理*。你可以单用其一，也可以两者并用。

!!! question "我真的需要插件吗？"
    **一开始不需要。** 对一次性任务，只需准备一个仓库并启动 `arbor`（见[准备基准](preparing-a-benchmark.md)）。
    只有当你**反复跑同一类基准**、并想让每次运行都用一模一样的评测规则、护栏与预算时，才动用插件。

## 激活一个插件

在你项目的配置里（`research_config.yaml`、`arbor.yaml` 或 `autoresearch.yaml`）放一行，然后从
项目目录启动交互式 CLI：

```yaml title="research_config.yaml"
plugin: mle_kaggle        # 切换领域唯一需要的一行
```

```bash
cd my_competition
arbor
```

Arbor 自动发现项目目录里的配置；接入对话随后就以插件的契约与指导启动。

!!! tip "不编辑文件也能试用插件"
    你也可以从接入对话里挑插件——输入 `/` 用斜杠命令：

    ```text
    /plugin load mle_kaggle mle_bench_lite   # 加载一个插件（及可选 profile）
    /plugin unload                           # 本次运行忽略已配置的插件
    /plugin reset                            # 回到你配置所指定的那个
    ```

    这些选择只对你即将启动的那一次运行生效，不改你的配置。

## 插件格式

每个插件都遵循**同一套标准形状**。最小的一个只需要一个名字和一份评测契约：

```yaml title="minimal_plugin.yaml"
name: my_domain
description: "One line on what this plugin optimizes"
schema_version: 1

eval_contract:
  metric_direction: maximize          # 或：minimize
  eval_cmd: "bash {cwd}/eval.sh"      # {cwd} -> 项目目录
```

其余一切都是可选的、叠加在上面的。完整字段集：

| 字段 | 必填 | 用途 |
| --- | --- | --- |
| `name` | ✓ | 插件标识符，由配置里的 `plugin:` 引用。 |
| `description` | ✓ | 在 `arbor` 插件列表中显示的一行摘要。 |
| `schema_version` |  | 格式版本（当前为 `1`）。 |
| `eval_contract` | ✓ | 怎么打分：`metric_direction`、`eval_cmd`（带 `{cwd}` 替换），以及可选的 `submission_path` / `sample_submission_path`。 |
| `protected_paths` |  | 对 executor 只读的 glob 模式——你的数据与评测框架。 |
| `required_outputs` |  | 一次运行要算有效，必须存在的产物。 |
| `profiles` |  | 具名的预算包（`max_cycles`、`max_tree_depth`、`executor_timeout`、`time_budget`），用 `plugin_profile` 选择。 |
| `config_overrides` |  | 插件为每次运行设定的默认配置值。 |
| Prompt 注入 |  | 合并进智能体系统提示的领域指导（见下文）。 |

### Prompt 注入点

领域指导在六个定义明确的点上加入——四个给 **coordinator**（研究总监），两个给 **executor**
（运行单个实验的工程师）：

| 键 | 注入到 |
| --- | --- |
| `meta_preamble_inject` | coordinator 提示顶部——总体目标与策略。 |
| `meta_init_inject` | coordinator 的发现/准备阶段。 |
| `meta_ideate_inject` | coordinator 的想法生成阶段。 |
| `meta_decide_inject` | coordinator 的合并/保留或剪枝决策。 |
| `sub_preamble_inject` | executor 提示顶部。 |
| `sub_workflow_inject` | executor 的工作流规则与护栏。 |

每个都是纯 markdown 文本。用它们来编码领域习惯（"总是先产出一个有效基线"、"绝不写入 `data/`"），
而不是去脚本化某个具体的解。

### 每个设置在哪生效

设置按固定优先级组合，从低到高：

```text
内置默认  <  plugin.config_overrides  <  profiles[active]  <  你的 YAML 配置  <  CLI 参数
```

所以你在自己配置里设的值总是胜过插件，而 CLI 参数胜过一切。

## 随附的范例：`mle_kaggle`

Arbor 随附一个插件 `mle_kaggle`，作为面向 Kaggle / MLE-bench 竞赛的完整范例。它声明了评测契约、
保护数据与评测框架、要求一个 `submission.csv`，并附带一份基准预算 profile：

```yaml title="src/plugins/mle_kaggle.yaml (节选)"
name: mle_kaggle
description: "Engineering optimization for Kaggle/MLE-bench competitions"
schema_version: 1

eval_contract:
  metric_direction: maximize
  eval_cmd: "bash {cwd}/eval.sh"
  submission_path: "submission.csv"
  sample_submission_path: "data/sample_submission.csv"

protected_paths:
  - "data/**"
  - "private/**"
  - "evaluation/**"

required_outputs:
  - "submission.csv"

profiles:
  mle_bench_lite:                 # 24 小时 MLE-Bench-Lite 预算
    max_cycles: 20
    max_tree_depth: 4
    executor_timeout: 14400       # 每个 executor 4 小时
    time_budget: 86400            # 总计 24 小时
```

把 profile 与插件一并选定：

```yaml title="research_config.yaml"
plugin: mle_kaggle
plugin_profile: mle_bench_lite
```

一份可直接编辑的配置位于仓库的 `examples/kaggle_config.example.yaml`。

## 编写你自己的插件

1. 在你项目里建一个 `plugins/` 文件夹，并在其中加入 `my_domain.yaml`：
   `<project>/plugins/my_domain.yaml`。Arbor 从这个文件夹发现项目插件（内置插件随 Arbor 一起，
   也总是可用）。从上面的最小模板起步，或复制随附的 `mle_kaggle` 插件作为范例。
2. 为你的领域设定 `name`、`description` 与 `eval_contract`。
3. 如果你的任务有要守护的数据或要产出的产物，加上 `protected_paths` / `required_outputs`。
4. 加一个带你算力预算的 `profiles` 条目。
5. 只有当你需要领域特定行为时，才用注入点去调校智能体。

然后按名激活它——配置里 `plugin: my_domain`，或对话里 `/plugin load my_domain`——并启动 `arbor`。
当你想塑造智能体*如何*推理（而不只是它优化什么）时，把它和一个[技能](skills.md)搭配使用。
