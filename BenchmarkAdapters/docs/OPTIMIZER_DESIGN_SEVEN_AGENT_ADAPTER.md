# modded-NanoGPT Optimizer Design 七 Agent Adapter

## 当前状态

Optimizer Design 已按 Autoresearch 的外层搜索架构完成两层 Adapter：

1. `BenchmarkAdapters/OptimizerDesign/` 是 Benchmark 公共层，统一拥有 protocol、
   revision store、dev broker、candidate policy、隔离 evaluator、双 held-out 计分、资源锁、
   runtime attestation、不可覆盖记录、三 seed 聚合和七 Agent scorecard。
2. `BenchmarkAdapters/OptimizerDesign/agents/` 是 Agent 小适配层，只选择 Agent 原生搜索后端，
   并把公共 Optimizer Design task contract 注入该后端。

七个 Agent 是 EAR、MLEvolve、Arbor、Codex、Claude Code、ML-Master 2.0 和
AiScientist。公共层不允许小适配器覆盖 evaluator、分数、seed、硬件或资源策略。

当前状态是 **command-ready，但 formal-blocked**。原因不是 Adapter 缺失，而是冻结的双
held-out baseline 记录仍是 `pending`。在该记录晋级、Adapter 提交并保持 clean、且完成
`7 × 3 = 21` 个正式 cell 之前，不得声称已有七 Agent 公平正式排名。

## 冻结协议

- 上游仓库：`https://github.com/KellerJordan/modded-nanogpt.git`
- 上游 commit：`bc1b58e83fa499c5df268bd6c8b98701273b96e7`
- Track 3 tree：`05bdf00394b7dee564500e9a6fdb472ce67a1659`
- 唯一 artifact：`records/track_3_optimization/train_gpt_simple.py`
- 当前 protocol digest：`75ace9d075c90559c953da2b9f7bab40c7564819bcf5ae7dc833e68234c0d3ac`
- 数据：官方 `kjj0/fineweb10B-gpt2` 的 20 个 train shard 和固定 val shard
- 外层模型：GPT-5.5，`high`，temperature `1.0`
- 外层预算：每个 Agent 每个 outer seed 172800 秒，outer seeds 为 `0,1,2`
- candidate 超时：7200 秒
- held-out seeds：`314159,271828`
- 硬件：一次 evaluation 独占同一台机器的四张 H100 80GB
- 失败分：`3801` steps

协议只允许修改 optimizer 实现、optimizer 超参数和 schedule、模型初始化，以及协议范围内
的字面量 `train_steps`。模型结构、数据路径、distributed setup、训练循环和计分输出均由
Benchmark 公共层冻结或注入保护。

## 验证和 Dry Run

```bash
PY=BenchmarkAdapters/.venv/bin/python
PROTOCOL=optimizer-design/protocol/protocol.json

$PY -m BenchmarkAdapters optimizer-design-protocol --validate "$PROTOCOL"

for agent in ear mlevolve arbor codex claude-code ml-master-2 ai-scientist; do
  $PY -m BenchmarkAdapters optimizer-design \
    --agent "$agent" --protocol "$PROTOCOL" \
    --output-dir "/tmp/optimizer-design-$agent" --seed 0 --dry-run
done
```

Dry run 验证每个 Agent 的原生命令和公共 task contract，不调用模型 API，也不生成可比较分数。

## 生成并晋级 Baseline

必须在四张目标 H100 都空闲时执行。四个 `--gpu-id`、`--cpu-set` 和
`--memory-limit-gib` 在后续七 Agent 正式 campaign 中必须保持一致。

```bash
$PY -m BenchmarkAdapters optimizer-design-baseline \
  --protocol "$PROTOCOL" \
  --gpu-id 0 --gpu-id 1 --gpu-id 2 --gpu-id 3 \
  --cpu-set 0-63 --memory-limit-gib 128 \
  --output-dir /runs/optimizer-design-baseline
```

命令成功后，人工审阅记录，再把完整 evidence 与 completed record 一起晋级：

```bash
cp -a /runs/optimizer-design-baseline/baseline-evidence \
  optimizer-design/protocol/
cp /runs/optimizer-design-baseline/completed-baseline-score-record.json \
  optimizer-design/protocol/baseline_score_record.json

rm -f /tmp/optimizer-design-protocol.json
$PY -m BenchmarkAdapters optimizer-design-protocol \
  --output /tmp/optimizer-design-protocol.json
mv /tmp/optimizer-design-protocol.json optimizer-design/protocol/protocol.json
$PY -m BenchmarkAdapters optimizer-design-protocol --validate "$PROTOCOL"
```

晋级会改变 baseline 文件 SHA 和 protocol digest；所有正式 run 必须使用晋级后的新 digest。

## 正式 Campaign

先提交并冻结 Adapter tree，确认 Agent source 与 Adapter source 都 clean。然后为每个 Agent
运行 seeds `0,1,2`；未指定 `--smoke` 或 `--pilot` 时 CLI 固定使用正式 48 小时预算。

```bash
$PY -m BenchmarkAdapters optimizer-design \
  --agent ear --protocol "$PROTOCOL" \
  --output-dir /runs/optimizer-design/ear/seed-0 --seed 0 \
  --gpu-id 0 --gpu-id 1 --gpu-id 2 --gpu-id 3 \
  --cpu-set 0-63 --memory-limit-gib 128
```

每个 cell 的最终 candidate 会在搜索结束后冻结，并仅由 host-owned evaluator 对两个 held-out
seed 复测。Agent 在搜索阶段只能访问 development capability，不能读取 held-out seed、
held-out 输出或其他 Agent 的 workspace。

## 聚合与公平性

```bash
for agent in ear mlevolve arbor codex claude-code ml-master-2 ai-scientist; do
  $PY -m BenchmarkAdapters optimizer-design-aggregate \
    --protocol "$PROTOCOL" --campaign-dir /runs/optimizer-design \
    --agent "$agent" --output "/runs/optimizer-design/$agent/aggregate.json"
done

$PY -m BenchmarkAdapters optimizer-design-scorecard \
  --protocol "$PROTOCOL" --campaign-dir /runs/optimizer-design \
  --output /runs/optimizer-design/scorecard.json
```

只有同时满足以下条件，scorecard 才会设置
`complete_seven_agent_comparison_valid=true`：

- 七个 Agent 各有同一 protocol 下的三个有效正式 seed；
- 每个 run 都绑定同一冻结 source、data、environment、evaluator 和 baseline；
- 每个 Agent 的三个 seed 使用同一 Agent commit，全部 run 使用同一 Adapter commit；
- GPU 型号/容量、Python/Torch/CUDA、CPU affinity、内存限制、模型 endpoint identity 一致；
- artifact、manifest、stdout、evaluation 和 held-out trajectory 的哈希与重算分数全部一致。

Arbor 论文的 4xA100/private-workspace 数字会被明确排除，不能混入这个内部 4xH100
reconstruction 榜单。
