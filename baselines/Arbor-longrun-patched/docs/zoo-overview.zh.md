# 基准库（Benchmark Zoo）

Arbor 的工作流程是:针对一个可评分的任务,迭代地修改代码、运行评测,并保留能提升分数的改动。
**基准库**是一组此类任务的集合,每个任务以统一格式打包,可直接交由 Arbor 优化。它位于仓库的
[`arbor-zoo/`](https://github.com/RUC-NLPIR/Arbor/tree/main/arbor-zoo) 目录下,每个基准一个文件夹。

## 主要用途

- **可直接运行的优化任务。** 每个基准自带评测脚本与基线实现,将 Arbor 指向它即可开始优化。
- **接入你自己的任务。** 若你已有代码但缺少可运行的评测,一条命令即可补全评测脚手架。
- **自动收集(开发中)。** 用一句话描述需求——点名一个工作("帮我获取 WebThinker 用到的数据集"),或一个目标("我想用自一致性方法刷 GPQA")。由 agent 找到数据集/benchmark,**询问你要哪个数据集、baseline 从哪来**(收割现成的、按你描述的方法实现、或上网找现成实现),获取数据并产出一个可运行草稿;也可直接给一个 repo 地址。

## 基准的组成

每个基准是一个目录,包含以下部分:

- **README** —— 任务说明:任务内容、优化的指标、Arbor 可修改的范围。供 Arbor 在接入阶段读取。
- **基线实现** —— 优化的起点,也是 Arbor 唯一可修改的部分(如 `solution.py`)。
- **评测脚本** —— 运行后打印一行 `score:`;该脚本受保护,Arbor 不可修改。

Arbor 的迭代循环为:修改基线 → 运行评测 → 若分数提升则保留,循环往复。

## 使用入口

| 用途 | 命令 |
| --- | --- |
| 列出库中的基准 | `arbor benchmark list` |
| 在某个基准上运行 Arbor | 将其拷出仓库,`git init` 后在目录内运行 `arbor` |
| 校验一个基准的结构 | `arbor benchmark verify <目录>` |
| 将你的代码补全为可运行的基准 | `arbor benchmark scaffold <目录>` |
| 查找并构建一个 benchmark | `arbor benchmark add "<需求>"`(或 repo 地址) |

运行示例:

```bash
cp -r arbor-zoo/algotune_knn /tmp/algotune_knn   # 拷出 Arbor 仓库
cd /tmp/algotune_knn && git init -q && git add -A && git commit -qm baseline
arbor                                             # 确认任务后开始迭代
```

## 进展

- **已支持:** 基准格式、校验(`verify`)、列表(`list`)、脚手架(`scaffold`)、收集主体(`add`),
  以及首个示例基准 `algotune_knn`。
- **进行中:** 增强 `add`(一句话需求 → 找到数据集、问你要哪个/baseline 从哪来、产出可运行草稿),以及补充更多基准。

撰写一个基准的详细格式见[格式参考](zoo.md);整体规划见[路线图](roadmap.md)。
