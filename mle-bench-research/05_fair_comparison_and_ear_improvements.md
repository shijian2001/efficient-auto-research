# 五、公平对比、EAR 改进与归因分析（2026-07）

> 涵盖：转发代理架构、公平性审计、两轮 12h 实验（不公平轮 → 公平轮）的完整结果、
> EAR 四项改进的设计与实测、mlsp/chaii 两题的深度归因、下一步方向。
> 数据与工件索引见文末。前置背景：文档四（12h 长跑归因）、02 §5.5（公平性口径）。
>
> **历史范围**：本文记录到 G1。G2/G3 见[文档六](06_stagnation_heated_ts_and_full_six_tasks.md)，
> G4/G5 见[文档七](07_g4_failure_and_g5_infrastructure.md)。
>
> 本文记录的是历史实验，不代表当前 7 Agent × 2 Benchmark 正式评测已经完成；当前状态以
> `BenchmarkAdapters/docs/SEVEN_AGENT_BENCHMARK_REPAIR_PLAN.md` 为准。

---

## 1. 基础设施：转发代理架构（零侵入评测）

当前活跃 Agent 为 **EAR、纯原版 MLEvolve、Arbor**；本文历史实验只比较 EAR 与 MLEvolve。早期两个参评 agent 的 LLM 适配曾散落在各自代码里（Bedrock 适配、重试、token 记录，改动 10+ 文件），
每次升级上游必然冲突。2026-07-03 重构为**本地转发代理**：

```
EAR / MLEvolve / Arbor
    │  OPENAI_BASE_URL → http://127.0.0.1:620X/v1   (端口 = 6200+GPU_ID, 每容器一个实例)
    ▼
BenchmarkAdapters/LLMRelay/server.py
    │  模型重写→gpt-5.5 · 强制 reasoning_effort=high · 剥 max_tokens 等参数
    │  重试20次 · 不限超时 · 非流式化+SSE合成 · tool-call JSON兜底 · system-only消息归一化
    │  token 独立记账 → run-logs/<TAG>_token_usage/<agent>_<comp>_gpu<N>.jsonl
    ▼
relay 上游 (gpt-5.5)
```

价值：agent 代码全部回到纯上游/自有分支；换模型只改环境变量；代理重试在两轮实验中兜住了全部上游抖动（合计 40+ 次重试，零 agent 层失败）。

## 2. 公平性审计（2026-07-04）

首轮代理架构 12h 跑完后做系统审计，发现 5 项不公平（详表见 02 §5.5）：

1. **EAR 带 mlsp 题目专属提示**（6-09 调试遗留的人类先验：用 tabular 特征/OvR 线性模型）
2. **MLEvolve 被省钱配置削弱**（drafts 3→2、parallel 3→1、debug_depth 20→5、
   global_memory 关、exec.timeout 32400→1800、漏跑官方 fusion 后处理）
3. **MLEvolve coldstart 知识库来路问题**：`competition_tag_classified.json` 硬编码
   MLE-bench 全部 75 题的竞赛→类别映射，属 benchmark 预计算适配（"读题"应是运行时能力）。
   双方约定关闭；`MLE_COLDSTART=1` 可复现其官方发布形态
4. **CPU 不对称**（EAR 放飞 256 核 vs MLEvolve 自绑 8 核且三容器互踩）
5. **单步超时不对称**（EAR 3600s vs MLEvolve 被压 1800s）

全部修复：MLEvolve 对齐官方 `run_single_task.sh`（仅关 coldstart），所有容器 cpuset
独占 21 核（=官方 CPUS_PER_TASK），EAR 删提示，各 agent 用自身发布默认。

## 3. 三轮 12h 实验总览（jigsaw / mlsp / chaii，官方 mlebench 评分）

| 轮次 | EAR jigsaw | EAR mlsp | EAR chaii | MLE jigsaw | MLE mlsp | MLE chaii |
|---|---|---|---|---|---|---|
| 0703 不公平轮 ⚠️ | 0.9823 | 0.9331*水分 | 0.7189 | 0.9869🥈 | 0.8897🥉 | 0.6635 |
| **0704 公平轮** | **0.98297** | 0.85789 | 0.71369 | 0.98231† | **0.92949🥈**† | **0.72932**† |
| 0710 改进轮(仅EAR) | 0.98236‡ | **0.88682🥉**‡ | 0.55128⚠ | —（对照沿用0704） | | |

† 含官方 fusion 后处理的更优值 ‡ 含 EAR 新 ensemble 的更优值

**公平轮正式结论：MLEvolve 2:1 胜（峰值高），EAR 用 4.2% token（236 万 vs 5609 万）。**
奖牌线参照：jigsaw bronze .9864 / median .9808；mlsp gold .9353 / silver .9004 /
bronze .8737 / median .8657；chaii median .7276。

关键副产结论：
- EAR mlsp 0.933→0.858 的下跌**定量证实**了题目提示的水分 ≈0.075
- MLEvolve fusion 白赚 +0.009~0.020（chaii 因此反超 EAR）
- MLEvolve 解析层修复彻底生效（旧轮 jigsaw/mlsp 有效节点 0% → 77-88%，三轮零解析失败）
- MLEvolve/mlsp 烧 4700 万 token 的机制：快题→12h 塞进 244 节点 × stepwise+debug 每次
  全量重发长上下文（93% 是 input）且无 prompt 缓存复用

## 4. EAR 四项改进（proxy-based-eval @ 107e437）

trace 分析（`analysis/traces_20260704_fair/`）定位公平轮 EAR 的四个问题 → 四项改进：

| # | 问题（fair 轮 trace 证据） | 改进 | 实测结果 |
|---|---|---|---|
| 1 | 探索过剩：mlsp step28 出 best 0.8406 后 12 步无人以它为 parent | **GP 自观测**：节点自身 metric 计入观测（修复"新 best 零观测被 TS 无视"的理论缺陷） | 行为生效（best 立即被 exploit）；mlsp 大赚 / chaii 反噬（见 §5） |
| 2 | 无 ensemble，单 best 提交 | **top-K ensemble**：metric 加权融合 top-5 submission，数值题生效/文本题回退，主提交不变 | ✅ 全线正增益：mlsp +0.020（**EAR 首枚 12h 奖牌🥉**）、jigsaw +0.001、chaii 正确跳过。离线回放 fair 轮数据同样验证（mlsp 0.858→0.928） |
| 3 | 同类低级 bug 反复出现（5+ 步 metadata 解析错误） | **错误记忆聚合**：按异常类型合并计数 + lesson 后缀，上限 8 条 | ✅ mlsp 有效步率 97%（历轮最高） |
| 4 | 每步 500+ 行全量重写 | **改进模式 prompt**：parent 有 metric 时要求"只改一个组件，勿重写" | ✅ 步数 41→88 翻倍（代码更短更稳） |

平衡原则：多样性由 TS 后验方差和 draft 机制守住（draft/debug prompt 不动），
确定性收益全部补齐；ensemble/错误记忆完全不触碰搜索策略。

## 5. 两题深度归因

### 5.1 chaii 退步（0.714→0.551）：自观测的反噬

chaii 首个有效分极低（~0.10 弱基线）。自观测让 GP 把低分区域当"已知最好"反复深耕，
而旧版的"漫游"恰能撞出 0.66 的方案跳变。**教训：exploit 修正对收敛型题（mlsp）大赚，
对需要方案跳变的题有害。** 对策不是回滚，是自适应保护：best 停滞 M 步或绝对值过低时，
临时放大后验方差恢复探索。

### 5.2 mlsp 仍输 MLEvolve（0.887 vs 0.929）：方案档次差距

| | EAR best | MLEvolve best |
|---|---|---|
| 方案 | 手工特征 + MIL + sklearn | **ImageNet 预训练 CNN 跑频谱图** + 特征 + LightGBM 分层集成 |
| 代码规模 | ~900 行 | **7325 行** |
| 单次执行 | 中位 78s | 中位 214s，高分节点 345s+ |
| 高分节点 | 0 个 >0.90 | 67 个 >0.90、29 个 >0.94 |

EAR 的 plan 里反复提到 BirdNET/CNN/spectrogram（方向它知道），但生成的代码总是退化成
轻量版。三个机制性原因：
1. **自设的复杂度纪律变成天花板**：prompt 里 "under 250 lines"、"finish within timeout"
   （当初为压 SyntaxError 而加）使模型不敢写重型方案
2. **单发生成撑不起 7000 行 pipeline**：MLEvolve 靠 stepwise 生成+20 层 debug+improve
   增量演化把复杂方案"搭"出来；EAR 的天花板 = 一次能写对的复杂度
3. 改进模式 prompt 让代码更稳的同时更保守（88 步无一步长训练）

### 5.3 汇总:EAR 的定位与差距本质

**EAR 输的两题输法不同**——chaii 输在探索/利用调度(可修),mlsp 输在方案表达力
(需要档位机制)。**赢的方式一致**:单位 token 效率 12-24 倍,收敛型题上分数持平或反超。

## 6. 下一步(优先级排序)

1. **停滞检测 + 双解锁**(一个机制解决两题):best 停滞 N 步时 → 同时
   (a) 放大 TS 后验方差恢复探索(治 chaii 锁死) (b) 解锁重型方案档——prompt 明示
   "允许 CNN/预训练/长训练,预算 X 秒"(治 mlsp 天花板)
2. 重型方案的两段式生成兜底(骨架→填充,仍远轻于 stepwise)
3. ensemble 保留为默认;考虑把 ensemble 结果本地 CV 验证后竞争主提交
4. 复跑 chaii/mlsp 验证;spooky/tweet/essay 三题尚未在新链路下跑过,可扩全 6 题

## 7. 工件索引

```
代码:     EAR proxy-based-eval 分支 @ 107e437 (github.com/VonEquinox/efficient-auto-research)
           MLEvolve = 纯上游 fe92521; coldstart=False
评测框架: docker-eval/run_in_docker.sh + BenchmarkAdapters/LLMRelay/server.py (用法见 docker-eval/README)
三轮报告: run-logs/20260703_12h_proxy_rerun/  20260704_12h_fair/  20260710_12h_ear_improved/
进度图:   analysis/plots/20260703_12h_proxy_rerun/  20260704_12h_fair/  (各12张)
trace:    analysis/traces_20260704_fair/ (人类可读汇总); 原始在各 agent 目录
token:    run-logs/<TAG>_token_usage/*.jsonl (代理逐调用记账)
```
