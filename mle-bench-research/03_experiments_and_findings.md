# 三、实验、Trace 分析与结论

> 涵盖：对比实验设计、5 步 / 30 分钟两轮结果、逐 trace 分析、两个 Agent 差异归因、公平评测设计、下一步计划。
>
> 📜 **历史存档**（Claude Sonnet 4.6 / Bedrock 时代）。分数与归因结论仍有效；文中涉及的
> Bedrock 适配、agent 侵入式修改均已被转发代理架构取代（见 docker-eval/README）。
> G4/G5 的后续状态见[文档七](07_g4_failure_and_g5_infrastructure.md)。

---

## 1. 实验设计

在 `spooky-author-identification`（最小 2MB 题）上对比搜索式 Agent。所有 Agent **统一用 Claude Sonnet 4.6 (Bedrock)**，同一数据，同一 mlebench official grading。指标为 log loss（越低越好）。

| Agent | 方法 | 搜索策略 |
|-------|------|----------|
| MLEvolve (#4) | Monte Carlo Graph Search | UCT + time-based phase switch |
| efficient-auto-research (自研) | Kernel Thompson Sampling | GP 后验采样 (cosine kernel) |

参考阈值：Gold 0.165 / Silver 0.270 / **Bronze 0.294** / Median 0.419。

---

## 2. 实验结果

### 5 步快跑

| 指标 | efficient-auto-research | MLEvolve |
|------|------------------------|----------|
| **Official Score** | **0.3573** | 0.4179 |
| Above median | ✅ | ✅ |
| 成功步数 | 2/5 (40%) | 1/5 (20%) |
| 首次得分时间 | **3.5 min** | 8 min |
| Token | 37,918 | ~50,000+ |

### 30 分钟对比

| 指标 | efficient-auto-research | MLEvolve |
|------|------------------------|----------|
| **Official Score** | **0.3663** | 0.6647 |
| Above median | ✅ | ❌ |
| 成功率 | 33% (2/6) | 10% (1/10) |
| 首次得分时间 | 3.5 min | **24 min** |

> efficient-auto-research 与 MLEvolve 均为 mlebench official grading。

**结论：** efficient-auto-research 在两种时间预算下均显著优于 MLEvolve（30min 时 0.37 vs 0.66，好约 45%），且更快、更省 token、成功率更高。

---

## 3. Trace 分析

### MLEvolve 30 分钟时间分配

| 阶段 | 时间 | 占比 | 产出 |
|------|------|------|------|
| Phase 1 串行生成 3 draft | 12 min | **40%** | 0 行代码执行 |
| Phase 2 前 7 步 | 8 min | 28% | 全 buggy |
| 第 8 步成功 | 4 min | 14% | metric=0.668（差） |
| 改进阶段 | ~3 min | — | 被 timeout 杀掉 |

**根本原因：**
1. Stepwise 分步生成（data→model→train→merge→review）产出 500-665 行复杂代码 → bug 多 → debug 循环长
2. Phase 1 必须串行生成完所有 draft 才能执行，12 分钟零产出
3. Code review 没拦住明显错误（`needs_revision=False` 但代码跑不起来）
4. 唯一成功的方案是 BERT + LR ensemble，BERT 只训 2 epoch + 4 层 frozen，欠拟合导致分数差

### efficient-auto-research 30 分钟

```
Step 0-1 (0-3.5min):  首次失败 → 立即修复成功 (best=0.389)
Step 2-5 (3.5-30min): 尝试 LightGBM/SGD ensemble 新方向，部分超时
```

**优势来源：**
1. 每步仅 2 次 LLM 调用（plan + code），产出 58-158 行精简代码
2. Prompt 引导 "simple, robust first approach" + 5-fold CV
3. Kernel TS 不陷入单点 debug 循环，能快速跳到新方向
4. 出错时从 parent 重新生成完整代码（而非 diff patch 一个破碎的 500 行文件）

### 核心对比

| 维度 | efficient-auto-research | MLEvolve |
|------|------------------------|----------|
| 每步 LLM 调用 | 2 次 | 5-7 次 |
| 生成代码量 | 58-158 行 | 485-665 行 |
| 冷启动时间 | 0 | 12 分钟 |
| 成功方案 | TF-IDF+LR (58行, CV) | BERT+LR (665行, 单 split) |

**差异不来自模型**（同为 Claude Sonnet 4.6），而来自架构 overhead：精简调用链 → 更高成功率 → 更好分数 → 更快。

---

## 4. 两个 Agent 跑通难度

| Agent | 改动文件 | 主要障碍 | 结果 |
|-------|---------|---------|------|
| efficient-auto-research | 1 个 | 无 | ✅ 顺畅，0.3573 (5步) / 0.3663 (30min) |
| MLEvolve | 1 个 | Phase1 overhead、代码过复杂 | ✅ 0.4179 (5步) / 0.6647 (30min) |
| AIBuildAI | — | 二进制 + 需交互式 TTY | ❌ 进程空转，已移除 |

---

## 5. AIBuildAI 局限性（重要结论）

AIBuildAI (#2, 63.11%) 是预编译二进制，架构上：

```
AIBuildAI → claude_agent_sdk → subprocess 调 `claude` CLI → Claude Code 的 API session
```

它**不直接 HTTP 调 LLM**，而是通过 Claude Agent SDK 调用本机的 `claude` 命令行（需 `claude login`）。这导致：
- 依赖交互式 TTY/PTY 环境
- 非交互后台运行（nohup）时 TUI 立即退出，`agent_calls: 0`，从未真正启动
- 进程不退而死循环空转：实测 PID 占 99% CPU 近 7 天、`run.log` 膨胀到 6GB

**启示：** 预编译 + CLI 中介的 Agent 在自动化批量评测环境中适配成本极高。相比之下，直接 HTTP 调 API 的开源 Agent（MLEvolve / efficient-auto-research）适配只需改 1 个文件。

---

## 6. 公平评测设计（待完善）

**已固定：** 同一 LLM、同一数据、同一 official grading。

**待统一：**

| 维度 | 方案 | 说明 |
|------|------|------|
| GPU | 固定单卡 (1× RTX 4090 / H800) | 目前各 Agent 对 GPU 假设不一 |
| 隔离 | Docker-in-Docker | 每个 Agent 独立容器，隔离包环境 |
| 时间 | 30min / 1h / 4h 三档 | 观察不同预算下的收敛曲线 |
| 重复 | 每配置 3 次取均值 | 减少随机性 |
| 指标 | Any Medal% / Score / Token 效率 / 首次有效提交时间 | 多维度 |

---

## 7. 下一步

1. **跑完 6 题**：efficient-auto-research 与 MLEvolve 各跑 6 题，验证区分梯度
2. **固定评测框架**：Docker 隔离 + 统一 GPU/时间/token 计量
3. **多题对比**：6 题 × 2 Agent × 3 次 = 36 次实验，画收敛曲线（metric vs time / token / steps）
4. **论文方向**：Kernel Thompson Sampling 在 ML Agent 搜索中的样本/时间/token 效率优势，控制变量对比 UCT vs KTS

---

## 8. Trace 文件位置

```
efficient-agent-research/
├── mle-bench-agents/efficient-auto-research/runs/
│   ├── spooky/            # 5 步
│   └── spooky_30min/      # 30 分钟
├── baselines/MLEvolve/runs/
│   ├── 20260525_164921_spooky_claude_v3/   # 5 步 (journal.json + verbose.log)
│   └── spooky_30min.log                     # 30 分钟
└── mle-bench-agents/efficient-auto-research/
    └── run_spooky_v7.log  # 历史 EAR trace
```
