# 一、MLE-Bench 调研与选题

> 涵盖：基准测试概况、评测机制、排行榜、Agent 生态、区分度分析、最终 6 题选题。
>
> **时效说明（2026-07-22）**：六题选型继续使用；排行榜是文档采集时的历史快照，
> 不是实时榜单。当前 EAR 状态和实验口径见[文档七](07_g4_failure_and_g5_infrastructure.md)。
>
> **当前评测边界（2026-08-16）**：本文记录的是历史六题选型，不代表当前
> 22 题 × 7 Agent 的 MLE-Bench Lite 正式 campaign 已完成。统一 Adapter 的协议、资产、
> 依赖和真实 smoke 状态以
> [`BenchmarkAdapters/docs/SEVEN_AGENT_BENCHMARK_REPAIR_PLAN.md`](../BenchmarkAdapters/docs/SEVEN_AGENT_BENCHMARK_REPAIR_PLAN.md)
> 为准。

---

## 1. MLE-Bench 是什么

| 项目 | 详情 |
|------|------|
| 名称 | MLE-Bench: Evaluating Machine Learning Agents on Machine Learning Engineering |
| 机构 | OpenAI |
| 论文 | arXiv:2410.07095 (2024-10)，ICLR 2025 |
| 代码 | https://github.com/openai/mle-bench |

基于 **75 个真实 Kaggle 竞赛**评估 AI Agent 的端到端 ML 工程能力（数据理解 → 模型训练 → 预测输出），覆盖图像/文本/表格/音频/分割/检测/推荐等领域。

**数据规模：**

| 版本 | 竞赛数 | 数据大小 | 难度 |
|------|--------|----------|------|
| Full | 75 | ~3.3 TB | Low 22 / Medium 38 / High 15 |
| Lite | 22 | ~158 GB | 仅 Low |

**进展速度：** 论文发布时（2024-10）最佳为 o1-preview + AIDE = 16.9% Any Medal；当前（2026-02）最佳为 Famou-Agent 2.0 (Gemini-3-Pro) = 64.44%。一年半提升约 4 倍。

---

## 2. 评测机制

### 核心指标：Any Medal (%)

Agent 在所有竞赛中获得至少一枚奖牌（铜/银/金）的比例。每个竞赛对比 Kaggle 历史真实排行榜判定奖牌：

- **金牌**：前 10% 或 top 10（取较小）
- **银牌**：前 5% 或前 50 名
- **铜牌**：前 40%

### 评分流程

1. Agent 在 Docker 环境运行，产出 CSV 预测文件
2. 用竞赛对应指标（AUROC/RMSE/LogLoss/F1 等）评估
3. 与历史排行榜对比确定排名 → 判定奖牌
4. 含抄袭检测（防止复制公开 notebook 或记忆的解法）+ 规则违规检测

### 官方标准评估环境

| 要求 | 规格 |
|------|------|
| 运行次数 | ≥3 seeds，报告 mean ± standard error |
| 时间限制 | 24 小时 |
| CPU / RAM | 36 vCPUs / 440 GB |
| GPU | 1× NVIDIA A10 (24GB) |

---

## 3. 排行榜（22 个 Agent，截至 2026-04）

标准条件：24h，36 vCPU / 440GB / 1× A10，≥3 seeds。

| # | Agent | LLM | Low% | Med% | High% | Overall% | 时间 | 开源 |
|---|-------|-----|------|------|-------|----------|------|------|
| 1 | Famou-Agent 2.0 | Gemini-3-Pro | 80.3 | 64.0 | 42.2 | **64.44** | 24h | ✗ |
| 2 | AIBuildAI | Claude-Opus-4.6 | 77.3 | 61.4 | 46.7 | 63.11 | 24h | ✗ |
| 3 | CAIR MARS+ | Gemini-3-Pro | 78.8 | 60.5 | 44.4 | 62.67 | 24h | ✗ |
| 4 | **MLEvolve** | Gemini-3-Pro | 80.3 | 57.9 | 42.2 | 61.33 | 12h | ✓ |
| 5 | PiEvolve | Gemini-3-Pro | 80.3 | 58.8 | 40.0 | 61.33 | 24h | ✗ |
| 7 | ML-Master 2.0 | Deepseek-V3.2 | 75.8 | 50.9 | 42.2 | 56.44 | 24h | ✗ |
| 8 | CAIR MARS | Gemini-3-Pro | 74.2 | 52.6 | 37.8 | 56.00 | 24h | ✗ |
| 16 | InternAgent | deepseek-r1 | 62.1 | 26.3 | 24.4 | 36.44 | 12h | ✗ |
| 17/20/22 | R&D-Agent | gpt-5 / o3 / o1 | — | — | — | 22-35 | — | ✓ |
| 19 | AIRA-dojo | o3 | 55.0 | 22.0 | 21.7 | 31.60 | 24h | ✓ |

**关键观察：**
1. **LLM 主导**：Top 10 里 7 个用 Gemini-3-Pro，基础模型能力是关键
2. **框架也重要**：同样 Gemini-3-Pro，不同框架差距可达 12%
3. **High 是瓶颈**：最好的 Agent High 也仅 42-47%
4. **开源可复现的**：MLEvolve(#4)、R&D-Agent、AIRA-dojo

### 候选 Agent 的方法论

- **MLEvolve (#4)**：Monte Carlo Graph Search + UCT。并行 3 路树搜索，LLM 生成完整脚本作节点，分支融合 + 全局记忆(BM25+FAISS) + 冷启动知识库。12h 达 #4，时间效率最高。
- **AIBuildAI (#2)**：Manager-driven multi-agent，预编译二进制，内部用 Claude Agent SDK 调 Claude Code CLI（详见文档三的局限性分析）。

---

## 4. 区分度分析：Medium 是关键战场

对前 10 Agent 在三个难度上的得分做统计：

| 指标 | Low | Medium | High |
|------|-----|--------|------|
| 极差 (Max-Min) | 12.1% | **19.3%** | 11.1% |
| 变异系数 | 4.97% | **12.18%** | 7.98% |
| 对应区分题数 | ~2.7 题 | **~7.3 题** | ~1.7 题 |

**结论：**
- **Low**：天花板效应，前 5 名几乎打平（~18/22 题），是"入门门槛"非竞争焦点
- **Medium**：从 64% 到 45% 分布最广，~7 题差距，**真正拉开 Agent 差距的战场**，决定 Overall 排名
- **High**：题数少(15)且普遍做不好，对排名区分贡献有限

---

## 5. 逐题通过数据（8 Agent × seed1）

从 GitHub LFS 的 grading report 提取 8 个 Agent（#1 Famou / #2 AIBuildAI / #3 MARS+ / #8 MARS / #10 Leeroo / #16 InternAgent / #12 STAR1.5 / #19 AIRA）的逐题奖牌情况，用于挑选**有区分度**的题目。

**关键发现：**
- **全过题**（8/8，无区分度）：spooky-author、mlsp-2013-birds、google-quest、herbarium-2020、inaturalist-2019 等
- **全不过题**（0/8，太难）：tgs-salt、ventilator-pressure、AI4Code、cdiscount、icecube 等
- **区分题**（2/8~6/8，能分层）：见下方选题

---

## 6. 最终选题：6 题（Low 3 + Medium 3）

### 选题原则

1. Low 3 + Medium 3，覆盖强/中/弱区分度
2. 数据量尽量小（优先 < 100 MB），GPU 需求轻
3. 包含基线验证题 + 区分题

### 选定的 6 题

| # | 难度 | 竞赛 | 数据 | GPU | 通过率 | 评估指标 | 定位 |
|---|------|------|------|-----|--------|----------|------|
| 1 | Low | spooky-author-identification | 2 MB | CPU | 8/8 | Log Loss | 基线验证 |
| 2 | Low | mlsp-2013-birds | ~557 MB | CPU/GPU | 8/8 | AUC | 音频领域 |
| 3 | Low | jigsaw-toxic-comment-classification | 53 MB | CPU/GPU | 6/8 | Mean Col ROC AUC | 区分题 |
| 4 | Med | chaii-hindi-and-tamil-qa | 7 MB | GPU轻 | 3/8 | Jaccard | 强区分 |
| 5 | Med | tweet-sentiment-extraction | 1.4 MB | GPU轻 | 5/8 | Word Jaccard | 中区分 |
| 6 | Med | learning-agency-lab-essay-scoring-2 | 12 MB | GPU轻 | 6/8 | QWK | 弱区分 |

**总数据量：~630 MB**（实测下载后 prepare 共 3.5 GB，含中间产物）。

### 区分梯度与 Agent 分档

```
全过 ──── 多数过 ──── 少数过
 8/8        6/8    5/8    3/8
 spooky     jigsaw tweet  chaii
 mlsp-birds essay
```

| 档位 | 通过题数 | 对应 Agent 水平 |
|------|----------|----------------|
| S | 6/6 | Famou-Agent 2.0 / AIBuildAI 级别 |
| A | 5/6 | Leeroo 级别 |
| B | 3-4/6 | MARS / InternAgent 级别 |
| C | 1-2/6 | MLE-STAR / AIRA 级别 |

### 各区分题的难点

- **jigsaw-toxic (6/8)**：多标签 NLP。MARS+(#3) 不过而 AIRA(#19) 过，考验具体多标签策略而非整体实力。
- **chaii-hindi-tamil (3/8)**：仅 30MB 却最难。难在非英文(Hindi/Tamil)需选对多语言预训练模型(mBERT/XLM-R) + 抽取式 QA pipeline。Leeroo(#10) 能过说明其框架对 NLP 有特殊优化。
- **tweet-sentiment (5/8)**：仅 5MB，考验精确 token-level span 提取。
- **essay-scoring (6/8)**：清晰分水岭——#1~#16 全过(且全 Gold)，仅 #12/#19 不过。区分"能否做基本 NLP 回归"。
