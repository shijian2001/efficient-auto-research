# 基准库 —— 格式参考

本页说明一个基准目录的格式,以及 `arbor benchmark verify` 的检查项。整体介绍见
[总览](zoo-overview.md)。

本格式以文档为中心:一个基准即一个文档完备的目录。README 为 Arbor 在接入阶段读取的自然语言说明,
无需填写 YAML 清单。

## 目录组成

每个 `arbor-zoo/<name>/` 包含以下内容:

| 文件 / 目录 | 作用 | 面向 |
| --- | --- | --- |
| `README.md` | 任务说明:任务内容、指标、Arbor 可修改的范围、dev/test 的划分方式(自然语言)。 | Arbor(及人) |
| 基线实现 | 可修改的基线 —— `solution.py`,或一组文件。 | Arbor 修改 |
| `eval.sh` *或* `eval.py` | 受保护的评测入口。`bash eval.sh dev\|test`(或 `python eval.py --split …`)打印一行 `score: <float>`。 | 受保护 |
| `PROVENANCE.md` | 来源、运行环境、基线说明、污染评估、注意事项。 | 人工查阅 |
| `data/`、`task.py` 等 | 评测所需的数据 / 参考实现(受保护)。 | — |

以 `_` 开头的目录(如 `_template`)为脚手架,工具会自动跳过。

### `README.md` —— 任务说明

README 是 Arbor 理解任务的依据,其读取方式与接入任意代码仓库时一致。无固定模板,一份说明通常包含
以下四部分:

1. **任务** —— 任务内容,以及一个解的形态。
2. **指标** —— 评测打印的内容(一行 `score:`),以及越大或越小为优。
3. **可修改范围** —— 作为基线的文件;其余内容(评测脚本、参考实现、数据)均不可修改。
4. **dev / test** —— 两者的划分方式,以明确留出集(不相交的随机种子,或 `data/dev/`、`data/test/`
   两个目录)。

格式中**不包含固定的基线分数**:同一基线在不同硬件 / 模型下分数不同,因此基线写入 PROVENANCE,而非
固化为一个数值。

### `PROVENANCE.md` —— 来源说明

必含章节(校验器检查其是否存在):**Source**、**Setup & environment**、**Baseline**、
**Contamination assessment**、**Caveats**。来源、许可证、基线的实现方式(及分数的波动范围)、留出集的
说明等,均在此记录,供维护者审阅。

## `arbor benchmark verify` 的检查项

`verify` 为轻量的**结构**检查,用于确认组成完整,而非正确性门禁,且**不运行评测**(基线分数并非通用
数值)。它检查:

- `README.md` 存在且非空;
- `PROVENANCE.md` 存在且必含章节齐全;
- 评测入口(`eval.sh` 或 `eval.py`)存在。

```bash
arbor benchmark verify arbor-zoo/<name>   # 缺少任一项即以非零码退出
arbor benchmark list arbor-zoo            # 列出基准
```

dev/test 是否真正留出、基线的实际行为等,记录于 PROVENANCE 并由人工判断,不做机器强制。

## 在基准上运行 Arbor

Arbor 在仓库根目录的 git worktree 中运行实验,因此请在 Arbor 仓库**之外**的副本中操作:

```bash
cp -r arbor-zoo/algotune_knn /tmp/algotune_knn
cd /tmp/algotune_knn
git init -q && git add -A && git commit -qm baseline
arbor   # Arbor 读取 README、确认任务后开始迭代
```

## 新增一个基准

1. 生成脚手架:`arbor benchmark scaffold arbor-zoo/<name> --style zoo`。该命令写入评测占位、
   `solution.py` 占位、自然语言 `README.md` 与 `PROVENANCE.md`,但不会生成解法本身。
2. 补全基线(`solution.py`)、评测(`eval.py` / `eval.sh`)、README(供 Arbor)与 PROVENANCE(供人工)。
3. 反复运行 `arbor benchmark verify arbor-zoo/<name>` 直至以 0 退出,再由维护者审核接受。起草可自动化,
   **接受为人工环节**。

完整示例见
[`arbor-zoo/algotune_knn`](https://github.com/RUC-NLPIR/Arbor/tree/main/arbor-zoo/algotune_knn)。
