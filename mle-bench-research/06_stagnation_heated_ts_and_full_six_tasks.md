# 六、停滞自升温 TS、全 6 题与 MLEvolve 官方 trace 对比（2026-07-13→15）

> 承接 [文档五](05_fair_comparison_and_ear_improvements.md)。文档五记录到 G1（imp 四项改进）——
> mlsp 摘首枚奖牌但 chaii 因自观测反噬崩到 0.551。本文记录之后的两代修复（G2 停滞自升温、
> G3 metric-sign），把新扩的 3 题（spooky/tweet/essay）纳入，并首次与 MLEvolve **官方 trace**
> 逐题对比。数据与工件索引见文末。
>
> **历史范围**：本文截至 G3。第 6 节是跨版本历史最好值汇总，不是 G5 或任何单一 commit
> 的主实验结果。G4 失败与 G5 基础设施见[文档七](07_g4_failure_and_g5_infrastructure.md)。
>
> 本文记录的是历史实验，不代表当前 7 Agent × 2 Benchmark 正式评测已经完成；当前状态以
> `BenchmarkAdapters/docs/SEVEN_AGENT_BENCHMARK_REPAIR_PLAN.md` 为准。

---

## 1. EAR 四代演化总表

| 代 | 日期 | commit | 别名 | 核心改动 | 净效果 |
|----|------|--------|------|---------|--------|
| G0 | 07-04 | `fee7df0` | fair | 删 mlsp 提示，与 MLEvolve 完全对称 | 干净基线，三题无牌 |
| G1 | 07-10 | `107e437` | imp | GP 自观测 + top-K ensemble + 错误聚合 + 改进模式 prompt | mlsp 🥉；**chaii 崩 0.714→0.551** |
| **G2** | 07-13 | `1b386cd` | **stag** | **停滞自升温 TS** + 持久化缓存 prompt + 生成温度 1.0 | **chaii 修复→0.728**；mlsp 升 🥈0.924 |
| **G3** | 07-15 | `5d1ca2a`+`7cd9ed5` | metric-sign | 搜索方向自适应 + 初始化顺序修复 | spooky 校正 🥈0.215 |

> G0/G1 的完整设计与归因在文档五。本文聚焦 G2/G3。

---

## 2. G2：停滞自升温 Thompson Sampling（治 chaii 挖地）

### 2.1 根因（imp 版 chaii/mlsp trace 分析）

commit `1b386cd` 的数据分析定位了 imp 版 chaii 0.588→0.541 退步与普遍低步数的**两个独立根因**：

1. **过度利用 / 盆地锁死**：自观测（G1 的 `107e437`）把 TS 拉向当前 best，探索方差被压缩 4 倍
   （0.041→0.011），chaii 此后再没突破 0.55。跨 22 次跑，best 中位停滞 40–60% 的步数且无逃逸
   机制。mlsp 同病：88 步里 42% 卡在 0.73 盆地。
2. **chaii 每步从头重算**：84% 墙钟耗在 GPU 训练；25 步内 muril-base 和 xlm-r-base 各被重新
   fine-tune 25 次、外部数据 curriculum 阶段重复 20+ 次，checkpoint 全丢弃。12h 只买到 25 步 →
   样本太少，搜索根本没空间探索。

### 2.2 改动（纯搜索/prompt 层，不碰执行引擎）

- **`thompson.py`（+42）停滞自适应探索温度**：`select_parent()` 新增 `stagnation` 计数。当
  best 连续 > `STAGNATION_TRIGGER`(3) 步未提升，把后验**采样**方差乘温度 T：

  ```
  T = 1 + STAGNATION_GAIN(0.5) × max(0, stagnation − 3)   , 上限 STAGNATION_T_MAX(3.0)
  stagnation ≤ 3  →  T = 1  →  行为与改进前完全一致
  ```

  T 上限 3.0 ≈ fair 轮的高方差状态。**纯搜索层旋钮，不动 LLM 生成温度。** 单元行为测试：
  父节点多样性随 stagnation 0→7 从 4/10 提升到 10/10。
- **`search.py`（+45）**：维护 `self._stagnation`（best 改善清零，否则 +1）传入 `select_parent`；
  给 code system prompt 加「持久化缓存」段，指向 `work_dir/cache`，带 load-or-recompute 守卫
  （通用措辞，无题目先验，不改复杂度上限）。
- **`llm/__init__.py`（+2）**：默认生成温度 0.7→1.0（常数；与上面的搜索层升温正交）。

### 2.3 实测（官方 mlebench 评分）

| 题 | imp（G1） | **stag（G2）** best | stag ensemble | 变化 |
|----|-----------|---------------------|---------------|------|
| chaii | 0.55128 ⚠️ | **0.72768** ⬜过median | —（文本题正确回退） | **+0.176，修复且超 fair 的 0.714** |
| mlsp | 0.88682 🥉 | 0.91905 🥈 | **0.92431 🥈** | **+0.037，升银牌** |

- **chaii 挖地问题彻底解决**：0.551 → 0.728，首次越过 median 线（0.72756）。local best 也从
  0.541 回到 0.653（17 步）。自升温让搜索重新「跳」出低分盆地，验证成功。
- **mlsp 顺带升银**：同一锁死病被同一机制治好，0.887🥉 → 0.924🥈，几乎追平我们跑的满血
  MLEvolve（0.929）。72 步，286 万 token。

---

## 3. G3：metric-sign 方向修复（治 spooky log-loss）

新扩题 spooky 的指标是 **multiclass log-loss（越低越好）**，但 EAR 的搜索早期把所有 metric 当
「越高越好」，导致对 spooky 这类题选父方向反了。修复分两个 commit：

- **`5d1ca2a` 方向自适应（B1）**：`search.py`(+86)、`thompson.py`(+21)。用 LLM 在启动时判定该题
  metric 的方向（升/降），整条搜索链据此调整（TS argmax 方向、best 判定、ensemble 加权）。
- **`7cd9ed5` 初始化顺序修复**：`search.py`(+20)。方向探针的 LLM 调用原本跑在 token 计数器
  创建之前，静默回退默认方向、且不记账——修复后先建计数器再探针。

> 记忆：这正是 [[ear-metric-direction-bug]] 与 [[ear-init-order-llm-token-bug]] 两条。

实测：spooky G3 重跑（`20260715_dirfix`）best 0.2174、ensemble **0.2150 🥈**（35 步，71 万 token），
超过 3newtasks 轮未修方向时的结果。

---

## 4. 新扩 3 题（spooky / tweet / essay）

这 3 题此前从未在新代理链路下正式跑过（文档五 §6 待办）。2026-07-14 首跑（`20260714_3newtasks`，
spooky 后经 G3 重跑）：

| 题 | EAR best | EAR ensemble | 取用 | 步数 | token |
|----|----------|--------------|------|------|-------|
| spooky ↓ | 0.2174 | **0.2150 🥈**（G3） | ensemble | 35 | 71万 |
| tweet ↑ | **0.71899 🥈** | （无 ensemble 文件） | best | 30 | 72万 |
| essay ↑ | **0.83724 🥇** | None（回退） | best | 44 | 129万 |

essay 直接拿**金牌**（gold 线 0.83583），tweet/spooky 银牌。3 题 EAR 全部拿牌。

---

## 5. 与 MLEvolve 官方 trace 的逐题对比 ★

文档五之前只有「我们复现的 MLEvolve」。本轮找到了 **MLEvolve 提交给 MLE-bench leaderboard 的
官方 trace**：OpenAI mle-bench 仓库 `runs/mlevolve_group{1,2,3}/grading_report_group_*.json`
（Gemini-3-Pro-preview, 12h, 21 vCPU, 1×H200, 3 seeds），即 leaderboard #4 = 61.33% 那次成绩。
每个 group 是一个 seed 的全 73 题 grading report，含逐题 score + 奖牌判定。

> 注意：官方论文/GitHub README 只公开 75 题**整体奖牌率**（65.3% / 61.33%），**没有**逐题表；
> 逐题数据只能从这个 trace 里提。MLEvolve 官方仓库自身 `runs/` 在 `.gitignore` 里，不附 trace。

| 题目 | EAR（我们最好版） | MLEvolve（我们跑 gpt-5.5） | MLEvolve 官方 trace（seed1/2/3） |
|------|-------------------|----------------------------|----------------------------------|
| spooky ↓ | **0.2150 🥈** | 0.2463 🥈 | 0.19718🥈 / 0.22395🥈 / 0.22212🥈 |
| tweet ↑ | **0.71899 🥈** | 0.71354 ⬜ | 0.71765🥈 / 0.71732🥉 / 0.71574⬜ |
| essay ↑ | **0.83724 🥇** | 0.83570 🥈 | 0.83666🥇 / 0.83715🥇 / 0.8389🥇 |
| jigsaw ↑ | 0.98297 ⬜（fair） | 0.98231 ⬜ | 0.98684🥈 / 0.98805🥇 / 0.98771🥇 |
| mlsp ↑ | 0.92431 🥈（G2） | 0.92949 🥈 | 0.93498🥈 / 0.9512🥇 / 0.94387🥇 |
| chaii ↑ | 0.72768 ⬜（G2） | 0.72932 ⬜ | 0.71756⬜ / 0.67262⬜ / 0.75908🥈 |

官方奖牌线（该 trace 内记录，供判定）：spooky bronze .29381/median .41879；tweet .71705/.71378；
essay .83471/.82827；jigsaw .98639/.98079；mlsp .87372/.86572；chaii .73725/.72756。

### 关键读数

1. **官方满血 MLEvolve（Gemini-3-Pro）很强**：6 题稳拿 5 题奖牌（essay 金×3、spooky 银×3、
   jigsaw/mlsp 金×2 银×1、tweet 银铜），只有 chaii 波动（1 银 2 无）。
2. **我们复现的 MLEvolve（gpt-5.5 代理）明显弱于官方**——同题往往掉一档。说明把 LLM 从
   Gemini-3-Pro 换成 gpt-5.5 对 MLEvolve 这种「堆搜索+集成」的打法伤害很大。
3. **EAR（同 gpt-5.5 口径）反而经常贴近甚至追上官方 MLEvolve**：essay EAR 0.83724🥇 与官方
   0.837~0.839🥇 同档；spooky 0.215🥈 落在官方 0.197~0.224🥈 区间内；tweet 0.71899🥈 **高于**
   官方最好 seed 0.71765。在 token 效率领先 12–24 倍的前提下，这是很强的效果证据。

---

## 6. G0–G3 历史六题最好成绩（跨版本汇总）

| 题 | EAR 分 | 牌 | 版本来源 |
|----|--------|----|---------|
| essay | 0.83724 | 🥇 | 新3题（G1 引擎） |
| spooky | 0.2150 | 🥈 | G3 dirfix |
| tweet | 0.71899 | 🥈 | 新3题 |
| mlsp | 0.92431 | 🥈 | G2 stag |
| jigsaw | 0.98297 | ⬜过median | fair（G0） |
| chaii | 0.72768 | ⬜过median | G2 stag |

**合计 1 金 3 银，6 题全部过 median。** 对比 G1（imp）时代只有 mlsp 一枚铜——两代修复
（G2 自升温、G3 方向）+ 新 3 题把 EAR 从「1 铜」推到「1 金 3 银」。

---

## 7. 当时待办（已由文档七接续）

1. **六题尚未在统一版本下复跑**：chaii/mlsp = G2、spooky = G3、jigsaw/tweet/essay 仍是更早引擎
   （新3题用 G1 引擎、jigsaw 用 G0/G1）。应把 G2+G3 合并版跑满全 6 题，得到单一口径的干净成绩。
2. **mlsp 方案档次仍输官方**（EAR 0.924 vs 官方满血 0.95）：EAR 的复杂度纪律 prompt（当初为压
   SyntaxError 而加的「≤250 行 / 按时完成」）变成天花板，写不出 7000 行重型 pipeline。下一步
   **双解锁**：停滞时既放大探索方差（已做，治 chaii），又解锁重型方案档 prompt（待做，治 mlsp），
   见文档五 §6.1。
3. **MLEvolve 复现 vs 官方差距**需确认是否纯 LLM 差异（gpt-5.5 vs Gemini-3-Pro），
   还是配置仍有残余不对称。

---

## 8. 工件索引

```
历史 G2/G3 代码: ear-worktrees/stagnation-cache/  (分支 iter/stagnation-cache)
            ├─ G2: 1b386cd (thompson.py 停滞自升温 / search.py / llm)
            └─ G3: 5d1ca2a + 7cd9ed5 (metric-sign 方向 + 初始化顺序)
            主仓库 proxy-based-eval 同步到 G3
G2 结果:    ear-worktrees/stagnation-cache/docker_runs/20260713_stagcache_{chaii,mlsp}/
新3题结果:  ear-worktrees/stagnation-cache/docker_runs/20260714_3newtasks_{spooky,tweet,essay}/
G3 spooky:  ear-worktrees/stagnation-cache/docker_runs/20260715_dirfix_spooky-author-identification/
token:      run-logs/{20260713_stagcache,20260714_3newtasks,20260715_dirfix}_token_usage/
官方 trace: mle-bench/runs/mlevolve_group{1,2,3}/grading_report_group_*.json  (逐题官方奖牌)
MLEvolve我们跑: baselines/MLEvolve/runs/20260714_152510*_{spooky,tweet,essay}/
前三题fair/imp: mle-bench-agents/efficient-auto-research/docker_runs/{20260704_12h_fair,20260710_12h_ear_improved}_*
评分:       docker-eval/grade.py <comp> <submission.csv>
当前 G5:    ear-worktrees/attempt-isolation-telemetry-v2/  (分支 ear/g5)
```
