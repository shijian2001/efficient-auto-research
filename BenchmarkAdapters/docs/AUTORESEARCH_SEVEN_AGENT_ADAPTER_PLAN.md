# Autoresearch Architecture Design 七 Agent 适配与横向评测规范

## 0. 文档状态

- 文档性质：Autoresearch Architecture Design 的已实现重建协议、运行规范、验收清单与
  Definition of Done。
- 当前实现状态：冻结 protocol、`train.py`-only revision store、断网 candidate evaluator、
  dev broker、双 held-out final gate、七个原生 Agent 入口、三 seed aggregate 和 scorecard CLI
  已实现，并通过不调用模型 API 的定向 contract/synthetic 测试。七个 Agent 已通过各自原生
  边界连接冻结的 GPT-5.5/high/temperature 1.0 模型 Adapter；五个 Python Agent 使用锁定
  专用运行时和只读 source snapshot，Codex/Claude Code 使用版本探针。当前服务器七路原生
  import/CLI probe、外部 benchmark Python、prepared assets 和 kernel cache 已使完整 preflight
  达到 `command_ready`。本次仍没有调用模型 API、执行七 Agent 真实 scored smoke 或运行
  7×3×48h 正式 campaign。
- 目标 Agent：EAR、MLEvolve、Arbor、Codex、Claude Code、ML-Master 2.0、AiScientist。
- 目标结果：七个 Agent 都能从同一冻结 baseline 出发，在相同硬件和预算下优化
  `train.py`，产出可复测、可审计的最终 artifact，并得到可直接横向比较的
  held-out `val_bpb`。
- 模型边界：每个 Agent 通过自己的模型 Adapter 统一使用 GPT-5.5/high/temperature 1.0，
  endpoint identity 以脱敏 digest 写入 manifest，credential 只通过进程环境进入外层 sandbox。
- 诚实边界：完成本文档不等于复现 Arbor 论文原始 Architecture Design 数字。论文使用的
  held-out seed 封装和部分执行细节未作为公开一键任务包提供。若无法取得原始资产，本项目
  的正式协议必须命名为 `autoresearch-architecture-reconstruction-v1`。

## 1. Benchmark 身份

### 1.1 冻结源码

正式 Benchmark 使用：

- 上游仓库：`https://github.com/karpathy/autoresearch.git`
- 固定提交：`228791fb499afffb54b46200aca536f79142f117`
- 仓库位置：`autoresearch/`
- 上游文件清单与 SHA-256：`autoresearch/config/source_manifest.json`
- 环境锁：`autoresearch/uv.lock`
- Python：UV-managed Python 3.10
- PyTorch：锁文件中的 `torch==2.9.1` CUDA 12.8 构建

正式 run 启动前必须验证所有上游文件哈希。任何源码、锁文件或 prepared asset digest
漂移，都必须生成新的 protocol ID，不能沿用已有结果。

### 1.2 任务定义

每个外层 Agent 获得同一份任务：

1. 阅读冻结的 `program.md`。
2. 只修改 `train.py`。
3. 使用 host-owned evaluator 运行候选。
4. 目标是最小化 `val_bpb`。
5. 搜索结束后声明一个最终 candidate revision。

`program.md` 在正式 campaign 中是只读任务说明，不允许不同 Agent 使用不同版本。

### 1.3 可编辑与只读边界

唯一正式可编辑文件：

```text
train.py
```

必须只读并做哈希校验：

```text
prepare.py
program.md
pyproject.toml
uv.lock
.python-version
prepared data shards
tokenizer.pkl
token_bytes.pt
frozen FlashAttention kernel cache
evaluator implementation
protocol and baseline manifests
```

禁止事项：

- 修改 `prepare.py` 中的 `TIME_BUDGET`、`MAX_SEQ_LEN`、`EVAL_TOKENS`、
  `make_dataloader` 或 `evaluate_bpb`；
- 修改、替换或生成另一套 tokenizer/data；
- 安装依赖、修改 lockfile 或让 candidate 从网络下载代码；
- 修改 evaluator、结果 parser、seed policy 或硬件配置；
- 用预计算输出、硬编码验证答案或跳过实际训练伪造 `val_bpb`；
- 从其他 Agent/run 读取 candidate、日志或 dev 结果。

## 2. 分数与有效性

### 2.1 单次 Candidate Evaluation

host-owned evaluator 在 disposable candidate copy 中执行等价于：

```bash
UV_PROJECT_ENVIRONMENT=<locked-env> \
HOME=<read-only-prepared-home> \
CUDA_VISIBLE_DEVICES=<exclusive-h100> \
python train.py
```

Evaluator 不接受 Agent 自报分数。它必须从实际进程输出解析以下字段：

```text
val_bpb
training_seconds
total_seconds
peak_vram_mb
mfu_percent
total_tokens_M
num_steps
num_params_M
depth
```

候选只有同时满足以下条件才获得有效 dev score：

- candidate revision 只改变 `train.py`；
- `train.py` 是普通文件，不是 symlink、device、FIFO 或超大异常文件；
- 进程在 evaluator hard timeout 内退出；
- 退出码为 0；
- 输出中恰有一个可解析且有限的 `val_bpb`；
- `training_seconds` 满足冻结 300 秒预算的容差规则；
- 没有 OOM、CUDA error、NaN/Inf、编译失败或 evaluator exception；
- 只读 Benchmark 资产在运行前后哈希一致；
- candidate digest、日志 digest 和 evaluator digest 完整写入结果。

`val_bpb` 越低越好。失败候选的 dev reward 记为无效，不得由 LLM 文本中的数字替代。

### 2.2 搜索期 Dev Score

搜索期 dev evaluator 用于父节点选择、candidate 淘汰和最终 artifact 选择。它不是最终主分。

每次 dev evaluation 记录：

- candidate ID、parent ID、`train.py` SHA-256；
- Agent、outer seed、candidate sequence number；
- evaluator seed identity；
- `val_bpb` 和完整运行指标；
- 开始/结束时间、GPU UUID、环境 digest；
- outer LLM token/cost 累计值；
- 成功、失败、超时、OOM 等结构化状态。

同一个 candidate hash 的重复 dev evaluation必须显式记录，不能静默复用旧分数。

### 2.3 最终 Held-out Score

搜索结束后 supervisor 冻结 dev-selected 最终 `train.py`。外层 Agent 进程、模型 credential
和 dev broker 全部关闭后，host 才能执行最终复测。

正式主分定义为：

```text
final_score = mean(val_bpb_seed_A, val_bpb_seed_B)
```

两次 held-out evaluation 均必须有效；任一失败时该 outer run 标记为
`invalid_artifact` 或 `failed_final_evaluation`，不得只用成功的一次作为主分。

最终报告同时保存两个 raw `val_bpb`、均值、差值、运行指标和日志，不能只保存平均数。

### 2.4 Held-out Seed 重建策略

当前上游 `train.py` 直接调用 `torch.manual_seed(42)` 和
`torch.cuda.manual_seed(42)`，公开仓库没有 Arbor 论文的 held-out seed wrapper。

公开条件下有两条可能路径：

1. **严格复现轨道**：取得论文使用的 seed 注入方式和两个 seed；或
2. **本地重建轨道**：实现 host-owned seed injection，并记录注入算法、注入位置、
   seed 列表、转换前后脚本哈希和测试。

本项目采用并已冻结第 2 条本地重建路径：dev seed 为 `42`，两个 sealed held-out seeds
为 `314159` 和 `271828`。Evaluator 在 disposable copy 中用 AST 转换替换受支持的
PyTorch seed hook，原始 final artifact 保持不变；候选代码还必须通过反射、动态导入、
seed-hook 覆盖和 `evaluate_bpb` 调用边界检查。

本地重建保证：

- seed 由 evaluator 注入，Agent 不能读取 held-out seed；
- seed 变化只影响随机状态，不改变模型、数据、预算和 evaluator；
- dev seed 与两个 held-out seed 互不相同；
- candidate 无法通过删除或覆盖 seed hook 固定最终随机性；
- seed injection 对七个 Agent 完全相同；
- 注入后的运行脚本可由原始 final artifact + evaluator version 确定性重建。

上述机制已通过无 API contract 测试，但这只解除 seed-policy 的实现阻塞。只有完成相同
协议下的三个 48 小时 outer runs、每个 artifact 的两次 held-out 复测和正式聚合后，才可
发布可横向比较的最终分。

## 3. 主公平协议

### 3.1 固定条件

主轨道冻结以下条件：

| 项目 | 正式设置 |
|---|---|
| Benchmark source | `autoresearch@228791f` |
| 可编辑文件 | 仅 `train.py` |
| 单候选训练预算 | 300 training seconds |
| 主指标 | 两个 held-out seed 的 mean `val_bpb`，越低越好 |
| 外层搜索预算 | 每个 Agent × outer seed 48 小时 wall-clock |
| 外层重复 | 至少 3 个预注册 seed，例如 `[0, 1, 2]` |
| GPU | 每个 outer run 独占同型号 H100 80GB |
| CPU/RAM | 固定 cpuset、CPU 数和 RAM 上限 |
| 并发 | 单个 candidate evaluation 独占一张 GPU |
| Prepared data | 同一只读数据和 tokenizer digest |
| Environment | 同一 UV lock、Python、CUDA、driver policy |
| 最终选择 | dev score 最低的有效 candidate；不得看 held-out score |
| 最终复测 | 每个 final artifact 两个 sealed seeds |
| 失败政策 | 失败保留在分母，不做 best-of-retry 替换 |

模型统一由各 Agent 模型 Adapter 和 formal manifest 门禁共同保证。

### 3.2 H100 调度

服务器有 4 张 H100，而主矩阵为 7 Agent × 3 outer seeds = 21 个 48 小时 run。

正式 campaign 不能让多个 outer run 共享同一张 GPU。推荐：

- 分批运行，每个 cell 独占一张 H100；
- 每个 seed block 随机化 Agent 顺序；
- Agent 在不同物理 GPU 之间做平衡轮换，降低单卡差异；
- 记录 GPU UUID、驱动、功耗/时钟策略和并发邻居；
- campaign 启动前统一预热 FlashAttention/kernel cache；
- 不把排队时间计入 outer budget，但从 Agent 启动到 supervisor 结束的时间全部计入。

最低正式计算量约为 `7 × 3 × 48 = 1008 H100-hours`，另加最终复测、baseline、
smoke 和基础设施重试。开始 campaign 前必须预留足够机器时间。

### 3.3 外层 Seed 与候选随机性

outer seed 控制 Agent 自己的搜索随机性、候选编号和原生 scheduler。它不等于训练 seed。

必须分别记录：

- outer search seed；
- dev evaluator seed；
- final held-out seeds；
- Agent 内部未能受控的随机源。

禁止把三个 outer run 中逐候选最好结果拼成一个“超级 run”。

## 4. Canonical Adapter 架构

### 4.1 实现目录

```text
BenchmarkAdapters/AutoResearch/
├── __init__.py
├── aggregate.py            # Avg@3、CI、效率指标
├── baseline.py             # 冻结上游 baseline 与 editable allowlist
├── broker.py               # Agent 可调用的 dev-only capability
├── dev_client.py           # sandbox 内的 capability client
├── evaluator.py            # host-owned 300 秒 evaluator
├── protocol.py             # protocol load/validate/digest
├── revisions.py            # train.py-only revision store
├── search.py               # Agent 与 supervisor 的统一搜索 contract
├── seed_injection.py       # host-owned dev/final seed policy
├── supervisor.py           # 48h、best freeze、sealed final evaluation
└── launchers/
    ├── __init__.py          # 七 Agent dispatch
    ├── common.py            # 原生命令与固定任务边界
    ├── proposal.py          # 单文件 proposal 应用
    ├── runner.py            # native command lifecycle
    ├── sandbox.py           # 断网、最小可见面的 Agent sandbox
    ├── ear.py
    ├── mlevolve.py
    ├── ml_master_2.py
    └── ai_scientist.py

autoresearch/protocol/
├── protocol.json
├── baseline_manifest.json
├── kernel_cache_manifest.json
├── evaluator_manifest.json
└── seed_policy.json
```

### 4.2 Supervisor 职责

所有 Agent 共用一个 host-owned supervisor。它负责：

1. 验证 protocol、source、environment、data、tokenizer 和硬件 digest；
2. 拒绝 dirty Agent/Adapter source；
3. 从冻结 baseline 创建 run-owned revision store；
4. 创建 dev evaluator broker；
5. 启动该 Agent 的原生控制循环；
6. 强制 48 小时 outer wall-clock；
7. 记录每次 candidate evaluation 和 token/cost；
8. 搜索结束后选择 dev 最优 revision；
9. 从 baseline replay 最终 `train.py`，验证 hash 和 allowlist；
10. 关闭 Agent、dev broker 和 credential；
11. 执行两个 sealed held-out seed；
12. 原子发布最终 artifact、manifest、result 和日志摘要。

Agent 不能自行执行最终 evaluator，也不能用 held-out 结果回滚或重选 candidate。

### 4.3 Revision Store

每个 candidate 是：

```text
candidate_id
parent_id
train.py SHA-256
unified diff
creation metadata
dev evaluation record
```

Revision store 必须：

- 每个 sibling 使用独立 workspace；
- 只允许 `train.py` 改动；
- 拒绝 symlink、路径逃逸、submodule 和特殊文件；
- evaluator 在 disposable copy 中运行，训练缓存不能回写 candidate；
- final replay 从 baseline + selected diff 重建，而不是复制 Agent 的活动工作目录；
- replay 后 hash 必须等于 selected revision hash。

### 4.4 Dev Broker

七个 Agent 使用同一个逻辑能力：

```text
evaluate_dev(candidate_revision_id) -> structured JSON
```

Broker 只接受 revision ID，不接受 Agent 提供的任意 host path或 shell command。返回：

```json
{
  "candidate_id": "...",
  "candidate_sha256": "...",
  "status": "completed",
  "score_valid": true,
  "val_bpb": 1.012345,
  "training_seconds": 300.1,
  "total_seconds": 335.2,
  "peak_vram_mb": 45200.0,
  "evaluator_digest": "..."
}
```

Broker 不返回 held-out seed、held-out 日志或最终结果。每次调用不可覆盖并写审计记录。

### 4.5 Candidate Evaluator

Evaluator 必须独立于 Agent 实现，不能 import Agent 控制逻辑。建议流程：

1. 从 revision store materialize disposable candidate；
2. 重新验证只有 `train.py` 与 baseline 不同；
3. 挂载只读 benchmark source、environment、data 和 tokenizer；
4. 使用独占 GPU 启动子进程；
5. 施加 hard timeout、CPU/RAM 和进程组管理；
6. 收集 stdout/stderr、exit code、GPU metrics 和 wall-clock；
7. 严格解析最终指标；
8. 验证 protected assets 未改变；
9. 写不可覆盖 evaluation JSON；
10. 删除 disposable working copy，保留日志与 digest。

## 5. 七个 Agent 的原生 Adapter

原则：共享 supervisor/evaluator/revision contract，不共享一个“通用 OpenAI tool loop + 七套
prompt”。每个 Adapter 必须调用该 Agent 自己的原生 scheduler 或控制循环。

### 5.1 EAR

复用：

- `SearchGraph` / `Attempt`；
- `agent.engine.thompson.select_parent`；
- G3 metric direction 与 stagnation-heated KTS；
- EAR 自己的 LLM plan/code generation 和 token accounting。

适配：

- 一个 Attempt 对应一个 `train.py` revision；
- parent selection 选择 parent revision；
- LLM 输出限定为 `train.py` unified diff；
- evaluator 返回的 `val_bpb` 是可信观测；
- KTS 使用 `metric_sign=-1`；
- sibling candidate 必须隔离；
- final best 由 supervisor replay 并复测。

禁止为了 Autoresearch 修改 Thompson posterior、温度规则或 parent selection 行为。

### 5.2 MLEvolve

复用：

- `AgentSearch`、`SearchNode`、Journal；
- draft/debug/improve/evolve 阶段；
- UCT/soft-switch selection；
- 原生 planner、feedback 和 candidate lineage。

适配：

- Kaggle code/submission backend 替换为单文件 `train.py` revision backend；
- metric 设为 minimize；
- feedback 读取 broker JSON；
- crash/OOM candidate进入原生 debug 分支；
- Autoresearch 不执行 submission fusion；最终产物只能选一个 replayable `train.py`。

### 5.3 Arbor

复用原生 Arbor coordinator/executor/idea tree 工作流。该 Adapter 是薄适配：

- workspace 只包含冻结 Autoresearch candidate；
- 任务说明来自冻结 `program.md` 和 protocol card；
- dev evaluator 暴露为 Arbor 可调用 evaluator/tool；
- 只允许修改 `train.py`；
- 固定 coordinator cycles、tree depth、turn 上限和 timeout，并写入 manifest；
- Arbor 只声明最终 candidate，最终复测由 supervisor执行。

不得把论文自报分数或未公开的论文 task wrapper 当作本项目已实现资产。

### 5.4 Codex

复用原生 Codex repository workflow：

- 在隔离 candidate repository 中启动；
- 任务 prompt 指向冻结 `program.md` 和 dev broker 命令；
- workspace-write 仅允许 `train.py` 和 run-owned scratch；
- supervisor 负责长时间续跑/恢复，所有会话都计入同一个 48 小时预算；
- Codex 不能直接访问 prepared data 之外的 host 文件或 final evaluator；
- 最终活动 workspace 仍需经过 allowlist validation 和 baseline replay。

### 5.5 Claude Code

复用原生 Claude Code repository workflow，公平边界与 Codex 相同：

- 隔离 candidate workspace；
- 只允许编辑 `train.py`；
- 通过 dev broker获取结构化 feedback；
- session/continuation 放在 run-owned目录；
- supervisor 强制 48 小时预算和 final freeze；
- permission bypass 只能发生在外层 sandbox 内，不能扩展到 host。

### 5.6 ML-Master 2.0

复用 EvoMaster 原生 Agent、Session、tool registry 和多阶段 workflow：

```text
draft -> research -> improve -> review -> final selection
```

适配：

- 新建 Architecture Design task/config builder；
- 所有阶段操作同一个 revision lineage；
- evaluator tool 只接受 candidate revision ID；
- 每个并行实验使用独立 sibling；
- 原生 context、trajectory 和 stage result 全部保存；
- finalizer 返回 best revision identity，不自行运行 held-out evaluator。

### 5.7 AiScientist

复用 AiScientist 的 Subagent、LLM client、ShellInterface、file tools、step loop 和 token usage。

新增 `ArchitectureDesignSubagent`：

- system/task prompt 来自冻结 protocol；
- shell/file tool 仅可操作 candidate `train.py` 和 run-owned logs；
- 新增 `evaluate_candidate`、`list_experiments`、`restore_candidate`、
  `complete_with_best` 等 typed tools；
- 不复用 Kaggle submission、PaperBench 或 Terminal direct-task domain 逻辑；
- supervisor验证其最终 candidate并执行 sealed final evaluation。

## 6. 正式记录与产物

### 6.1 Protocol

`protocol.json` 至少冻结：

- protocol ID 和 reconstruction/strict-reproduction 身份；
- Autoresearch source、`prepare.py`、`program.md`、`uv.lock` digest；
- prepared data/tokenizer manifest digest；
- FlashAttention kernel cache manifest digest；
- evaluator 和 seed policy digest；
- dev seed、两个 held-out seed identity；
- outer seeds；
- 300 秒 candidate budget和 evaluator hard timeout；
- 48 小时 outer budget；
- GPU/CPU/RAM policy；
- retry、failure、artifact 和 aggregation policy；
- editable allowlist；
- baseline score record。

### 6.2 RunManifest

每个 Agent × outer seed 写不可覆盖 manifest：

- protocol digest；
- Agent/Adapter commit 与 dirty 状态；
- Agent 原生 backend identity；
- model Adapter identity；
- outer seed、dev seed、sealed seed policy；
- GPU UUID、CPU set、RAM、driver、CUDA；
- wall-clock 和 candidate 上限；
- source/environment/data/evaluator digest；
- run ID 和开始时间。

### 6.3 Final Artifact

最终 artifact 不是整个活动 workspace，而是：

```text
artifacts/final/train.py
artifacts/final/train.py.sha256
selection.json
final-evaluation/seed-A.json
final-evaluation/seed-B.json
result.json
```

`train.py` 由 supervisor 从 baseline replay 后原子发布，禁止覆盖已有 run。

### 6.4 Result Schema

主结果字段：

- `status`、`score_valid`、failure reason；
- `final_val_bpb_mean`；
- 两个 held-out raw `val_bpb`；
- dev-selected `val_bpb`；
- valid/failed candidate counts；
- final artifact SHA-256；
- outer wall-clock；
- candidate GPU time和 evaluation count；
- LLM input/output/cache/reasoning tokens；
- API cost；
- time-to-best、candidate-to-best；
- manifest/protocol/evaluator digest。

## 7. Campaign 与统计

### 7.1 正式矩阵

```text
7 Agents × 3 outer seeds × 48 hours
```

每个 outer run 只产生一个最终 artifact 和一个 final score。

### 7.2 主表

每个 Agent 报告：

- Final held-out `val_bpb` Avg@3，越低越好；
- 标准差、min/max 和 95% CI；
- 3 个 outer run 的 raw final scores；
- formal success rate；
- invalid final artifact count。

不能把 dev score、单 seed 最优分或历史论文分放进本项目主比分列。

### 7.3 效率表

单独报告：

- 总 LLM tokens 和每百万 token 改进；
- dev evaluation 次数；
- 有效 candidate rate；
- H100 evaluator hours；
- time-to-best；
- baseline 到 final 的绝对/相对 BPB 改进；
- API cost。

效果排名和效率排名分开，不合成未经预注册的单一总分。

### 7.4 Baseline

未修改的初始 `train.py` 必须在同一环境和两个 final seeds 上运行，形成本项目 baseline。
论文中的 1.096/1.098 只作为外部参考，不替代本机 baseline。

## 8. CLI 接口

```bash
# 生成/验证冻结协议
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters autoresearch-protocol \
  --output autoresearch/protocol/protocol.json

# 单个 Agent × outer seed 正式运行
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters autoresearch \
  --agent ear \
  --protocol autoresearch/protocol/protocol.json \
  --prepared-root /srv/autoresearch-prepared \
  --kernel-cache-root /srv/hf-cache/models--varunneal--flash-attention-3 \
  --environment-python /srv/autoresearch-env/bin/python \
  --output-dir /runs/autoresearch/ear/seed-0 \
  --seed 0 \
  --gpu-id 0

# 低成本 command/synthetic smoke，不生成正式分
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters autoresearch \
  --agent ear --protocol autoresearch/protocol/protocol.json \
  --output-dir /runs/smoke/ear --seed 0 --dry-run

# 聚合一个 Agent 的三个 outer runs
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters autoresearch-aggregate \
  --protocol autoresearch/protocol/protocol.json \
  --campaign-dir /runs/autoresearch --agent ear

# 生成七 Agent 横向表
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters autoresearch-scorecard \
  --protocol autoresearch/protocol/protocol.json \
  --campaign-dir /runs/autoresearch
```

以上命令已经接入真实 `python -m BenchmarkAdapters` 入口。正式 `autoresearch` 运行会
fail-closed 校验 source、UV lock、prepared manifest、冻结 FlashAttention kernel cache、
evaluator implementation、seed policy、H100 80GB、
clean source 和不可覆盖输出；`--dry-run` 只输出命令与冻结协议，不启动 Agent、GPU、模型
API 或 candidate evaluator，也不能作为有效分数证据。

## 9. 测试与验收

### 9.1 Contract Tests

- protocol digest 稳定且不可覆盖；
- source/data/tokenizer/lock/evaluator digest 漂移时拒绝启动；
- revision store 只接受 `train.py`；
- symlink、路径逃逸、特殊文件和 protected file 修改被拒绝；
- evaluator parser 对正常、缺字段、重复字段、NaN、OOM、timeout 正确分类；
- Agent 自报 `val_bpb` 不会进入正式结果；
- final artifact 必须从 baseline replay；
- final evaluator在 outer Agent 退出前不可调用；
- held-out seeds 不出现在 Agent namespace；
- 失败结果保留且不可被同 run ID 覆盖。

### 9.2 Agent-native Tests

每个 Agent 至少有一个测试证明调用的是原生控制循环：

| Agent | 必须证明的原生组件 |
|---|---|
| EAR | `agent.engine.thompson.select_parent` |
| MLEvolve | `AgentSearch` / `SearchNode` / UCT selector |
| Arbor | Arbor coordinator/executor workflow |
| Codex | native `codex exec` repository loop |
| Claude Code | native `claude` repository loop |
| ML-Master 2.0 | EvoMaster `Agent.run` multi-stage workflow |
| AiScientist | native Subagent `run` + typed tools |

仅 import 成功、prompt 不同或共享通用 tool loop 不算原生适配完成。

### 9.3 Synthetic End-to-End

使用秒级 fake evaluator 验证：

- parent/candidate lineage；
- minimize metric selection；
- sibling isolation；
- timeout closeout；
- best replay；
- two-seed final gate；
- result/aggregate schema。

Synthetic 测试不能作为真实分数证据。

### 9.4 Real Scored Smoke

每个 Agent 至少完成一次真实 300 秒 candidate evaluation：

- 使用冻结环境和 H100；
- 产生有效 `val_bpb`；
- 生成 candidate record 和日志 digest；
- 只修改 `train.py`；
- final artifact可 replay；
- 不要求跑满 48 小时，但结果必须标记 `smoke`、`non_comparable=true`。

七个 real smoke 全部归档后，才能把 readiness 提升到 `real_smoke_ready`。

### 9.5 Formal Pilot

正式全量前先做：

```text
7 Agents × 1 outer seed × reduced outer budget
```

Pilot 仍使用真实 300 秒 evaluator，但 reduced outer budget 的结果不得进入正式 48 小时主表。
Pilot 用于验证调度、失败处理、token 统计和 final seed gate。

## 10. Readiness 与 Definition of Done

### 10.1 Readiness 层级

1. `source_ready`：源码、环境和 protocol 资产存在且 hash 正确。
2. `environment_ready`：锁环境、数据、tokenizer和 H100 smoke 通过。
3. `command_ready`：Agent Adapter 可构造并通过 contract/synthetic 测试。
4. `real_smoke_ready`：该 Agent 有真实 300 秒有效 candidate 证据。
5. `formal_protocol_ready`：有完整 48 小时 outer run、两个 held-out seed、不可变结果和
   聚合证据。

当前仓库实现已通过 `command_ready` 所需的离线 contract/synthetic 测试，但 readiness
仍由 `preflight` 按每个 Agent 的真实 runtime、prepared assets、clean source 和 durable
evidence 单独判断。`preflight` 现在实际 import 原生 Python 组件，而不是只检查 executable；
smoke/formal readiness evidence 还必须回溯到 artifact、manifest、双 held-out records 或有效
Avg@3 aggregate。当前服务器的外部 UV Python 3.10 环境、prepared data/tokenizer 和冻结
FlashAttention cache 已通过不训练、不调用 API 的资产验证；五个 Python Agent 的锁定 profile
及只读 source snapshot 已安装，Codex/Claude Code CLI 版本探针也已通过。完整 preflight 的
七个 Autoresearch cell 当前均为 `command_ready`；dirty worktree 仍会阻止正式 launch。
`baseline_score_record.json` 当前明确为 `pending`，因此正式 48 小时入口还会 fail-closed；
smoke/pilot 不受该门禁影响，完成最终数据范围上的 clean H100 baseline 后才能将记录冻结为
`completed`。
由于仍没有七 Agent 的真实 300 秒 scored smoke，
不得把任一 Agent 声称为 `real_smoke_ready` 或 `formal_protocol_ready`。

### 10.2 单 Agent 完成条件

- 使用原生控制循环；
- 冻结 clean source和 Agent commit；
- 只修改 `train.py`；
- evaluator、revision 和 final gate 全部通过；
- 至少一次 real scored smoke；
- 三个 48 小时 outer seeds 全部有结构化结果；
- 每个成功 final artifact完成两个 held-out seed；
- 失败 run 保留在正式记录中。

### 10.3 七 Agent 横向比较完成条件

- 21 个 outer cells 全部有 completed/failed 终态；
- 同一 protocol digest、source/data/tokenizer/evaluator digest；
- 同一硬件、预算、seed schedule 和 failure policy；
- 同一最终 artifact 和 held-out evaluation规则；
- 输出 Avg@3 主表、效率表、raw runs 和 missing/failed cells；
- 不使用 best-of-seeds、跨版本拼接或论文外部数字填补失败 cell；
- 所有结果可从 manifest、artifact hash 和 evaluator record 重算。

满足以上条件后，才能写“七个 Agent 已在 Autoresearch Architecture Design 上完成有效
横向比较”。在此之前，只能按 readiness 层级描述进度。

## 11. 实施与后续顺序

已完成：

1. 冻结 baseline/data/tokenizer/kernel/evaluator manifest 和 protocol digest。
2. 实现并测试 dev/final seed injection。
3. 实现 revision store、evaluator、broker、supervisor 和 sealed final gate。
4. 接入七个 Agent 的不同原生 dispatch/control-loop bridge。
5. 接入单 cell dry-run、aggregate、scorecard 和 preflight CLI。
6. 完成七 Agent GPT-5.5 模型 Adapter、token usage、typed tools 和 endpoint identity 门禁。
7. 接入真实 smoke、reduced pilot、CPU affinity/RAM 限制和 readiness evidence 发布。
8. 完成无 API contract、synthetic、七路 CLI 和环境资产验证。
9. 安装 EAR、MLEvolve、Arbor、ML-Master 2、AiScientist 锁定运行时并验证只读 source snapshot；
   验证 Codex 和 Claude Code CLI。

后续正式 rollout：

1. 在空闲独占 H100 上完成七 Agent real scored smoke。
2. 执行 reduced-budget formal pilot。
3. 冻结 clean release commit、正式 prepared-data scope、baseline 和最终 protocol digest。
4. 执行 `7 × 3 × 48h` 正式 campaign 与聚合。

## 12. 当前明确不做

- 不把当前 1-shard 部署 smoke 的 `val_bpb` 作为正式 baseline；
- 不把论文数字复制成本项目分数；
- 不允许 Agent 改 Benchmark/evaluator 来提高分数；
- 不在 clean source、独占同型号 H100、完整三 outer seeds 和双 held-out gate 未满足时发布正式分；
- 不把 dry-run、import test 或 synthetic evaluator 当成“已经适配跑通”。
