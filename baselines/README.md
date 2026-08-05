初步筛选的结果是

MLE-Bench Lite

MLRC-Bench

NanoGPT-Bench

Terminal-Bench 2.0

在4090的服务器上可以跑MLE-Bench Lite和Terminal-Bench 2.0

MLRC-Bench需要单卡48GB的服务器  
NanoGPT-Bench需要4*A100的服务器  


# Benchmark 与 Baseline
只有数据、计分方式、模型、运行时间和硬件都相同，分数才能直接比较。

代码状态只看 Agent 本身及其运行框架，不看底层模型：✅ 完整开源；⚠️ 开源但代码不全；❌ 未开源。

## 1. [MLE-Bench Lite](https://github.com/openai/mle-bench/)
22 个 Kaggle 任务，评估端到端机器学习工程能力。

**算力：** 1×24GB+ GPU（RTX 4090 可跑；官方参考 A10，Arbor 用 A100；正式同卡对比建议 H100）；36 vCPU；440GB RAM；每个任务最多 24 小时；至少用 3 组不同随机设置重复实验。

**Benchmark代码：**[✅ 完整开源](https://github.com/openai/mle-bench)

| Baseline | 代码状态 | 模型 | 获得任意奖牌 | 获得金牌 | 结果来自哪里 |
| --- | --- | --- | --- | --- | --- |
| AIDE | [✅ 完整开源](https://github.com/WecoAI/aideml) | o1-preview | 35.91 ± 1.86% | — | 官方主榜 |
| AIDE* Greedy | [✅ 完整开源](https://github.com/facebookresearch/aira-dojo) | o1-preview | 45.91% | — | AIRA 改写后的运行环境，不能和官方主榜直接比较 |
| AIRA MCTS | [✅ 完整开源](https://github.com/facebookresearch/aira-dojo) | o3 | 47.27% | 30.91% | AIRA 改写后的运行环境，不能和官方主榜直接比较 |
| AIRA-dojo | [✅ 完整开源](https://github.com/facebookresearch/aira-dojo) | o3 | 55.00 ± 1.47% | — | 官方主榜 |
| R&D-Agent | [✅ 完整开源](https://github.com/microsoft/RD-Agent) | GPT-5 | 68.18 ± 2.62% | — | 官方主榜，12h |
| MARS | [⚠️ 开源但代码不全](https://github.com/jfc43/MARS) | Gemini 3 Pro | 74.24 ± 1.52% | — | 官方主榜 |
| ML-Master 2.0 | [✅ 完整开源](https://github.com/sjtu-sai-agents/EvoMaster) | DeepSeek V3.2 | 75.76 ± 1.51% | — | 官方主榜 |
| AIBuildAI | [⚠️ 开源但代码不全](https://github.com/aibuildai/AI-Build-AI) | Claude Opus 4.6 | 77.27 ± 0.00% | — | 官方主榜 |
| MARS+ | [⚠️ 开源但代码不全](https://github.com/jfc43/MARS) | Gemini 3 Pro | 78.79 ± 1.52% | — | 官方主榜 |
| MLEvolve | [✅ 完整开源](https://github.com/InternScience/MLEvolve) | Gemini 3 Pro | 80.30 ± 1.52% | — | 官方主榜，12h |
| Famou-Agent 2.0 | [⚠️ 开源但代码不全](https://github.com/baidubce/FM-Agent) | Gemini 3 Pro | 80.30 ± 1.52% | — | 官方主榜 |
| Arbor | [✅ 完整开源](https://github.com/RUC-NLPIR/Arbor) | Gemini 3 Flash | 81.82% | 40.90% | 论文自报 |
| Arbor | [✅ 完整开源](https://github.com/RUC-NLPIR/Arbor) | GPT-5.5 | **86.36%** | **77.27%** | 论文自报 |


## 2. [Autoresearch（Arbor：Architecture Design）](https://github.com/karpathy/autoresearch)
固定训练 300 秒，用验证集每字节比特数（BPB，越低越好）评分。

**算力：** 单卡 NVIDIA；官方在 H100 验证。AIRA-Design 使用 1×H200；Arbor 论文未注明该任务的 GPU 型号。跨 GPU 的 BPB 不可横比。

**Benchmark代码：**[✅ 完整开源](https://github.com/karpathy/autoresearch)

**结果来源：**[Arbor 论文](https://arxiv.org/abs/2606.11926)；[AIRA-Design 论文](https://arxiv.org/abs/2605.15871)。

### Arbor 论文的评测方法

- **被修改的对象：** 使用默认的 `autoresearch` 仓库，并以 `program.md` 作为任务规范；唯一允许修改的产物是单文件训练脚本 `train.py`。
- **允许优化的范围：** 可以修改模型形状、注意力模式、初始化、优化器行为与超参数、学习率调度、batch size、模型大小和训练循环等完整训练配方。不能修改 `prepare.py`，尤其不能改其中的 `TIME_BUDGET`、`MAX_SEQ_LEN`、`EVAL_TOKENS`、`make_dataloader` 和 `evaluate_bpb`；也不能修改数据文件、Tokenizer 文件、`pyproject.toml`、`uv.lock` 或安装新依赖。
- **单次候选实验：** 评测器执行 `uv run train.py`。训练脚本强制执行 300 秒训练预算，主要指标是最终 `val_bpb`，越低越好。崩溃、超时、显存不足，或没有输出可解析 `val_bpb` 的实验均视为失败。
- **搜索预算：** Arbor、Codex 和 Claude Code 获得相同的初始材料、任务目标、评测器和 **48 小时 wall-clock** 预算。Codex 和 Claude Code 通过官方 `/goal` 模式持续运行；Arbor 默认使用 20 个 coordinator cycles、最大树深 2。
- **开发集与最终复测：** 搜索阶段只根据固定 development evaluator 的结果迭代；搜索结束后，将选出的最终 `train.py` 在 **2 个未参与搜索的随机种子**上重新运行，并对最终 `val_bpb` 取平均。
- **外层重复实验：** Arbor 论文说明，除非另有注明，每种随机方法独立运行 3 次，并报告 Avg@3 与标准差。这里的“3 次外层搜索”和最终方案的“2 个 held-out seed 复测”是两层不同的重复。

| Baseline | 代码状态 | 模型 | 调优阶段分数 ↓ | 最终复测平均分 ↓ |
| --- | --- | --- | --- | --- |
| 初始版本 | [✅ 完整开源](https://github.com/karpathy/autoresearch) | — | 1.096 | 1.098 |
| Codex | [✅ 完整开源](https://github.com/openai/codex) | GPT-5.5 | 1.089 | 1.083 |
| Claude Code | ❌ 未开源 | Claude Opus 4.6 | 1.033 | 1.033 |
| Arbor | [✅ 完整开源](https://github.com/RUC-NLPIR/Arbor) | Claude Opus 4.6 | **1.029** | **1.028** |


### AIRA-Design 论文的评测方法（条件不同，不能和上表直接比较）
每个 Agent 独占 1×H200。每个模型独立运行 10 次，每次最多运行 24 小时或尝试 500 个方案。

| Baseline | 代码状态 | 模型 | 分数 ↓ | 这个数字怎么算 |
| --- | --- | --- | --- | --- |
| 初始版本 | [✅ 完整开源](https://github.com/facebookresearch/aira-dojo) | — | 1.0121 | AIRA 运行环境中的默认分数 |
| Greedy Opus 4.5 | [✅ 完整开源](https://github.com/facebookresearch/aira-dojo) | Claude Opus 4.5 | 0.992 | 10 次结果的中位数 |
| Greedy Opus 4.6 | [✅ 完整开源](https://github.com/facebookresearch/aira-dojo) | Claude Opus 4.6 | 0.991 | 10 次结果的中位数 |
| Greedy Opus 4.6 + 文献资料 | [✅ 完整开源](https://github.com/facebookresearch/aira-dojo) | Claude Opus 4.6 | **0.990** | 10 次结果的中位数 |
| Greedy Opus 4.5 + 文献资料 | [✅ 完整开源](https://github.com/facebookresearch/aira-dojo) | Claude Opus 4.5 | 0.968 | 10 次中最好的一次，不代表平均水平 |


## 3. [modded-NanoGPT Optimizer Design](https://github.com/KellerJordan/modded-nanogpt)
Arbor 论文跑的是 **NanoGPT-Bench Track 3：Optimizer Design**，不是以 wall-clock 时间为指标的 NanoGPT 主 speedrun。Track 3 固定模型架构、数据集和训练脚本，只允许优化优化算法及其超参数，目标是用尽可能少的训练步数达到指定验证损失。

**算力：** 每次评测使用 4×A100-80GB，并执行官方 `run_eval.py`。Arbor、Codex 和 Claude Code 的外层搜索预算均为 48 小时；搜索结束后，最终优化器在另外 2 个未参与搜索的随机种子上复测。

**Benchmark代码：**[✅ 完整开源](https://github.com/KellerJordan/modded-nanogpt)

### Arbor 论文的评测方法

- **初始版本：** 使用 NanoGPT-Bench 官方调优后的 Muon 配置。`train_gpt_simple.py` 训练一个 124M 参数 GPT-2；Muon 用于 Transformer block 权重，AdamW 用于 embedding、输出投影和标量参数。该初始版本在 3325 steps 时首次达到 FineWeb `val_loss ≤ 3.28`。
- **可修改范围：** 只允许修改 `train_gpt_simple.py` 中的优化算法及其超参数。数据集、batch size 和模型架构必须保持不变；单独修改 `train_steps` 不算有效改进，并且不允许在一个 step 内执行多次 forward pass。
- **计分方式：** `run_eval.py` 启动训练并监测验证损失，在首次达到 `val_loss ≤ 3.28` 时立即停止；此时的 step count 即为分数，越低越好。如果始终没有达到目标，则赋予超过 7000 steps 的惩罚分。
- **开发集与最终复测：** 搜索阶段反复调用标准 development evaluator；搜索完成后，将选出的优化器在 2 个 held-out random seeds 上重新评测，最终分数为两次 step count 的平均值。
- **外层重复实验：** 除非另有注明，每种随机方法独立运行 3 次并报告 Avg@3 与标准差；这与每次搜索结束后的 2-seed 最终复测不同。

| Baseline | 代码状态 | 模型 | 调优时所需步数 ↓ | 最终复测平均步数 ↓ |
| --- | --- | --- | --- | --- |
| 调优后的 Muon 初始版本 | [✅ 完整开源](https://github.com/KellerJordan/modded-nanogpt) | — | 3325 | 3325 |
| Codex | [✅ 完整开源](https://github.com/openai/codex) | GPT-5.5 | 3325 | 3325 |
| Claude Code | ❌ 未开源 | Claude Opus 4.6 | 3275 | 3287.5 |
| Arbor | [✅ 完整开源](https://github.com/RUC-NLPIR/Arbor) | Claude Opus 4.6 | **3225** | **3237.5** |


## 4. [Terminal-Bench](https://www.tbench.ai/leaderboard/terminal-bench/2.1)
在 Docker 终端中评估长时程编码与工具使用能力。

**需要特别区分：** Arbor 论文没有让 Arbor、Codex 和 Claude Code 直接完成 Terminal-Bench 题目。它把 Terminal-Bench 2.0 变成了一个 **Harness Engineering 的 Autonomous Optimization 任务**：外层研究 Agent 在 48 小时内修改官方 `terminus-2` 解题 harness；真正进入 Docker 终端完成任务的是被修改后的 `terminus-2`，其内层 backbone 始终为 GPT-5.5。因此，Arbor 论文的 77.36% 不能与 Terminal-Bench 官方榜上的 Codex CLI、Claude Code 等“直接解题”结果混为同一种实验。

**算力：** Terminal-Bench 本身没有统一 GPU 型号或卡数，按各任务的 `task.toml` 配置，主要消耗 Docker、CPU、内存和模型 API。Arbor 论文使用官方 Harbor evaluator，开发集与测试集评测均开启 8 个并发 worker。官方排行榜的每题多次运行协议与 Arbor 论文自定义的 36/53 dev-test AO 协议不是同一个协议。

**Benchmark代码：**[✅ 完整开源](https://github.com/harbor-framework/terminal-bench-2)

### Arbor 论文的评测方法

- **初始被优化对象：** 完全不修改地使用 Terminal-Bench 2.0 官方发布的 `terminus-2` terminal-agent codebase，不额外增加接口。它采用 ReAct 风格循环，通过 tmux keystrokes 操作一个持久终端会话，内层 backbone 为 GPT-5.5。
- **外层研究 Agent：** Codex（GPT-5.5）、Claude Code（Claude Opus 4.6）和 Arbor（默认 coordinator 与 executor 均使用 Claude Opus 4.6）获得相同的初始代码、任务目标、评测器和 48 小时 wall-clock 预算。外层 Agent 的任务是改进 `terminus-2` 的控制逻辑，而不是亲自解题。
- **任务划分：** 将全部 89 道 Terminal-Bench 2.0 任务按难度分层后，抽取 36 道 development tasks 和 53 道 held-out test tasks，使两个 split 都覆盖 easy、medium、hard 难度。
- **搜索与测试：** 48 小时搜索期间只能在 36 道 development tasks 上迭代；53 道测试题不能用于搜索。每次外层搜索完成后，最终 harness 才在 held-out test set 上评测一次。开发集命令为 `HARBOR_N_CONCURRENT=8 python3 run_eval.py --data data/dev.json`，测试集命令为 `HARBOR_N_CONCURRENT=8 python3 run_eval.py --data data/test.json`。
- **允许与禁止修改的部分：** 可以修改 system prompt、每题附加指令、agent subclass、base agent 与 ReAct loop、response parser、terminal session management，以及 `agent` 或 `prompts` 目录下的新文件；不能修改 evaluation harness、dev/test 数据、API 配置和 baseline reference record。
- **评测器与指标：** 使用 Terminal-Bench 2.0 官方 Harbor evaluation harness，dev/test 均为 8 个 concurrent workers；指标是成功完成任务的比例，越高越好。未修改的 `terminus-2` 在 dev 上为 21/36，即 58.33%。
- **重复实验：** 除非另有注明，每种随机方法独立运行 3 次并报告 Avg@3 与标准差。也就是说，“test set 只评测一次”指每次独立搜索结束后只对最终产物评测一次，并不等于整个方法只做一次外层搜索。

#### Arbor 论文中的 Harness Engineering AO 结果

| 外层研究 Agent | 外层研究模型 | 被优化的内层解题 Agent | 内层解题模型 | Dev 通过率 | Held-out Test 通过率 |
| --- | --- | --- | --- | --- | --- |
| 无（初始版本） | — | 未修改的 `terminus-2` | GPT-5.5 | 58.33% | 69.81% |
| Codex | GPT-5.5 | Codex 修改后的 `terminus-2` | GPT-5.5 | 63.89% | 73.59% |
| Claude Code | Claude Opus 4.6 | Claude Code 修改后的 `terminus-2` | GPT-5.5 | **75.00%** | 71.70% |
| Arbor | Claude Opus 4.6 | Arbor 修改后的 `terminus-2` | GPT-5.5 | 72.22% | **77.36%** |

#### Terminal-Bench 2.0 官方榜的直接解题结果（与上表协议不同）

| Baseline | 代码状态 | 模型 | 通过率 | 结果怎么得到 |
| --- | --- | --- | --- | --- |
| NexAU-AHE | [✅ 完整开源](https://github.com/china-qijizhifeng/agentic-harness-engineering) | GPT-5.5 | 84.7% | Terminal-Bench 2.0 官方榜 |
| Codex CLI | [✅ 完整开源](https://github.com/openai/codex) | GPT-5.5 | 82.2% | Terminal-Bench 2.0 官方榜 |
| Terminus 2 | [✅ 完整开源](https://github.com/harbor-framework/harbor) | GPT-5.3-Codex | 64.7% | Terminal-Bench 2.0 官方榜 |
| Codex CLI | [✅ 完整开源](https://github.com/openai/codex) | GPT-5.2 | 62.9% | Terminal-Bench 2.0 官方榜 |
| Claude Code | ❌ 未开源 | Claude Opus 4.6 | 58.0% | Terminal-Bench 2.0 官方榜 |
| OpenHands | [✅ 完整开源](https://github.com/OpenHands/OpenHands) | Claude Opus 4.5 | 51.9% | Terminal-Bench 2.0 官方榜 |
| Mini-SWE-Agent | [✅ 完整开源](https://github.com/SWE-agent/mini-swe-agent) | Claude Sonnet 4.5 | 42.5% | Terminal-Bench 2.0 官方榜 |


## 5. [FML-Bench](https://github.com/qrzou/FML-bench)
统一执行环境，比较机器学习研究 Agent 的搜索效率。

**算力：** 每次运行使用 1×A100-80GB（论文配置）；同时跑 N 次才需要 N 张卡；每轮尝试 100 个方案，共 3 轮；完整测试包含 18 个任务。

**Benchmark代码：**[✅ 完整开源](https://github.com/qrzou/FML-bench)

| Baseline | 代码状态 | 模型 | 平均提升 ↑ | 胜率 ↑ |
| --- | --- | --- | --- | --- |
| AdaptiveSearch | [✅ 完整开源](https://github.com/qrzou/FML-bench) | GPT-5.4 | **0.208** | **58.6%** |
| AI Scientist v2 / TAS v2 | [✅ 完整开源](https://github.com/qrzou/FML-bench) | GPT-5.4 | 0.193 | 56.2% |
| Autoresearch | [✅ 完整开源](https://github.com/qrzou/FML-bench) | GPT-5.4 | 0.192 | 56.2% |
| AIDE | [✅ 完整开源](https://github.com/qrzou/FML-bench) | GPT-5.4 | 0.178 | 49.4% |
| OpenEvolve | [✅ 完整开源](https://github.com/qrzou/FML-bench) | GPT-5.4 | 0.151 | 52.8% |
| AI Scientist v1 / TAS v1 | [✅ 完整开源](https://github.com/qrzou/FML-bench) | GPT-5.4 | 0.132 | 40.4% |
| AIRA | [✅ 完整开源](https://github.com/qrzou/FML-bench) | GPT-5.4 | 0.132 | 28.4% |

# 安装文档、仓库链接与实际运行协议

> **本节是实际部署与执行时的统一口径。** 前面的表格用于说明 Benchmark 和论文结果；本节用于回答“仓库装什么、Agent 改什么、评测器跑什么、最后记录什么”。安装成功不等于复现成功。正式实验必须固定 Benchmark commit、Agent commit/CLI 版本、模型版本、硬件、预算、随机种子、可修改文件范围和评测器版本。

## 公共前置环境

五个 Benchmark 不应共用一个 Python 环境。建议为每个 Benchmark 和每个需要源码修改的 Agent 建立独立的 venv/conda 环境，并把安装完成后的 lockfile、`pip freeze` 或 `conda env export` 保存到实验记录中。服务器至少需要以下公共组件：

| 组件 | 官方链接 | 本项目用途 |
| --- | --- | --- |
| Git 与 Git LFS | [Git](https://git-scm.com/downloads)；[Git LFS](https://git-lfs.com/) | 锁定仓库 commit；下载 MLE-Bench 的 LFS 内容 |
| Docker Engine | [Docker 官方安装文档](https://docs.docker.com/engine/install/) | MLE-Bench 环境与 Terminal-Bench 隔离任务容器 |
| NVIDIA Container Toolkit | [NVIDIA 官方安装文档](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) | 让 Docker 任务访问 NVIDIA GPU |
| `uv` | [`uv` 官方安装文档](https://docs.astral.sh/uv/getting-started/installation/) | Autoresearch、Harbor、AiScientist 等项目的环境管理 |
| Miniconda/Conda | [Conda 官方安装文档](https://docs.conda.io/projects/conda/en/latest/user-guide/install/) | FML-Bench 的 harness 环境与各任务独立环境 |
| Node.js/npm | [Node.js 官方下载](https://nodejs.org/en/download) | 通过 npm 安装或锁定 Codex CLI 时使用 |
| `tmux` | [tmux 官方仓库](https://github.com/tmux/tmux) | `terminus-2` 通过持久 tmux session 操作终端 |

基础环境验收：

```bash
git --version
git lfs version
docker version
docker run --rm hello-world
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi
uv --version
conda --version
node --version
npm --version
tmux -V
```

其中 CUDA 容器 tag 只是验收示例；正式实验必须改成团队锁定且与驱动兼容的镜像 tag，并保存镜像 digest。API key、Kaggle 凭证和云端登录信息不得写入 Git 仓库或实验日志。

---

## 一页式结论：五个 Benchmark 到底跑什么

| Benchmark | 我们实际运行的任务 | 外层 Baseline Agent 要做什么 | 固定的被评测对象与指标 | 明确不跑什么 |
| --- | --- | --- | --- | --- |
| **MLE-Bench Lite** | 直接完成 22 个 Lite Kaggle 任务 | 读取题目和数据，写训练/推理代码，生成每题的 `submission.csv` | MLE-Bench 官方 grader；Any Medal、Gold Medal 等 | 不是优化另一个 Agent 的 harness；这里就是直接解 Benchmark |
| **Autoresearch：Architecture Design** | 对同一个 LLM 训练代码库做自主架构与训练配方优化 | 反复修改唯一可编辑文件 `train.py`，每个候选运行 300 秒 | 固定 `prepare.py`、数据、Tokenizer 和 `val_bpb` evaluator；越低越好 | 不是只把默认 `train.py` 跑一次，也不是改 Benchmark 或改 `prepare.py` |
| **modded-NanoGPT：Optimizer Design** | 跑 Arbor 论文中的 **NanoGPT-Bench Track 3 Optimizer Design** | 只优化固定训练脚本中的优化算法和超参数 | 固定架构、数据和 batch size；目标是以更少 step 首次达到 `val_loss ≤ 3.28` | **不跑 NanoGPT 主 speedrun，不以 wall-clock 为分数，不允许改模型架构** |
| **Terminal-Bench：Arbor Harness Engineering AO** | 优化官方 **`terminus-2` harness** | 外层 EAR / Arbor / Codex / Claude Code 等修改 `terminus-2` 的 prompt、ReAct loop、parser 和终端控制逻辑 | 内层始终由修改后的 `terminus-2 + GPT-5.5` 解 36 dev / 53 held-out test；指标为 pass rate | **不让外层 Agent 直接解 Terminal-Bench 并把该分数当 AO 分数**；直接跑 Codex CLI/Claude Code 只可用于 Harbor 冒烟测试 |
| **FML-Bench** | 在统一 harness 中完成机器学习研究代码优化 | 每个 Agent 在每个任务上迭代改进给定 baseline code | 完整协议为 18 个任务；使用官方 task metric、Normalized Improvement 和 process metrics | FML-Bench-Lite 的 8 个任务只能作为低成本代理，不应冒充完整 18-task 结果 |

### 三类任务不要混淆

1. **直接解题型：** MLE-Bench Lite。Agent 本身直接产出提交文件。
2. **Autonomous Optimization 型：** Autoresearch、NanoGPT Optimizer Design、Terminal-Bench Harness Engineering。外层 Agent 优化一个代码产物，再由固定 evaluator 评价该产物。
3. **统一研究 Agent harness 型：** FML-Bench。不同 Agent 通过 FML 的 Agent 接口，在同一组 ML 任务、步数预算和评分脚本下运行。

---

## 1. MLE-Bench Lite：安装与运行

### 链接

- [MLE-Bench 官方仓库](https://github.com/openai/mle-bench)
- [MLE-Bench Agent 接入说明](https://github.com/openai/mle-bench/tree/main/agents)
- [Arbor 论文](https://arxiv.org/abs/2606.11926)

### 安装

```bash
git clone https://github.com/openai/mle-bench.git
cd mle-bench

# 正式实验时替换成团队锁定的 commit/tag
git checkout <PINNED_MLE_BENCH_COMMIT>
git rev-parse HEAD

# 仓库中部分内容使用 Git LFS
git lfs install
git lfs fetch --all
git lfs pull

python -m venv .venv
source .venv/bin/activate
pip install -e .

# Kaggle API 凭证
mkdir -p ~/.kaggle
cp /secure/path/kaggle.json ~/.kaggle/kaggle.json
chmod 600 ~/.kaggle/kaggle.json

# 下载并准备 22 个 Lite 任务
mlebench prepare --lite

# 可选：构建官方基础环境
# 正式对比时所有 Agent 应使用同一镜像及其 digest
docker build --platform=linux/amd64 \
  -t mlebench-env \
  -f environment/Dockerfile .
```

### 安装验收

```bash
mlebench prepare --help
mlebench grade --help
mlebench grade-sample --help
docker image inspect mlebench-env --format '{{.Id}}'
```

至少选择一个 Lite 任务，确认其 public 数据、任务描述和 sample submission 均已生成，并用一个格式正确的 CSV 验证 grader 能正常读取：

```bash
mlebench grade-sample /abs/path/to/submission.csv <competition-id>
```

### 我们正式跑什么

- 完整运行对象是 **22 个 MLE-Bench Lite 任务**。
- 每个外层 Agent 获得相同的 competition description、prepared public data、基础镜像、GPU、CPU/RAM、模型、API 配置和 wall-clock 预算。
- Agent 必须在每题结束时生成符合 sample submission 格式的 `submission.csv`；统一用 `mlebench grade` 或 `mlebench grade-sample` 评分。
- 若按照当前实验规划在 RTX 4090 上运行，可以比较**本项目中同卡同条件重新运行的 Agent**；不能把 4090 分数直接当成 Arbor 论文单 A100 协议的严格复现。
- 每次运行必须保存：competition ID、Agent commit、模型 ID、seed/run ID、开始与结束时间、最终 CSV、grader 输出、容器镜像 digest 和完整日志。

### 不允许的混淆

- MLE-Bench Lite 不是 AO harness 优化任务；Agent 是直接完成 Kaggle/MLE 任务。
- 官方榜、AIRA 改写环境和我们本地 4090 环境的分数只能在协议完全一致时直接比较。

---

## 2. Autoresearch（Architecture Design）：安装与运行

### 链接

- [Autoresearch 官方仓库](https://github.com/karpathy/autoresearch)
- [官方任务规范 `program.md`](https://github.com/karpathy/autoresearch/blob/master/program.md)
- [Arbor 论文的 Architecture Design 协议](https://arxiv.org/abs/2606.11926)

### 安装

```bash
git clone https://github.com/karpathy/autoresearch.git
cd autoresearch

git checkout <PINNED_AUTORESEARCH_COMMIT>
git rev-parse HEAD

# 安装 uv；服务器已有 uv 时跳过
curl -LsSf https://astral.sh/uv/install.sh | sh

uv sync

# 一次性准备数据与 Tokenizer
uv run prepare.py

# 跑一次默认 baseline，确认 H100 环境、CUDA 和日志解析正常
uv run train.py | tee baseline.log
grep '^val_bpb:' baseline.log
```

### 我们正式跑什么

我们运行的是 **Arbor AO Suite 中的 Architecture Design**：

- 外层 EAR、Arbor、Codex、Claude Code或其他已适配 Agent 在同一个仓库副本中工作。
- **唯一允许修改的主要产物是 `train.py`。** Agent 可修改模型形状、注意力、初始化、优化器、学习率、batch size、训练循环等训练配方。
- `prepare.py`、数据、Tokenizer、`pyproject.toml`、`uv.lock` 与 evaluator 必须只读。
- 每个候选方案都由固定命令 `uv run train.py` 运行，训练时间固定为 300 秒，读取最终 `val_bpb`，越低越好。
- 对齐 Arbor 论文时，每个外层 Agent 的总研究预算为 **48 小时 wall-clock**。
- 搜索结束后冻结最终 `train.py`，在 **2 个未参与搜索的 held-out seeds** 上重跑，最终分数为两次 `val_bpb` 的平均值。

### 推荐 workspace 权限

```text
autoresearch_ao/
├── train.py                 # 可编辑
├── program.md               # 任务说明；正式运行前锁定内容
├── prepare.py               # 只读
├── pyproject.toml           # 只读
├── uv.lock                  # 只读
├── data/tokenizer/cache     # 只读
├── run_eval_dev.sh          # 只读，内部执行 uv run train.py 并解析 val_bpb
└── private_test/            # 外层 Agent 不可访问；保存 held-out seed 配置
```

### 复现边界

公开 Autoresearch 仓库提供默认训练脚本、固定 300 秒 evaluator 和默认数据准备流程，但 **Arbor 论文使用的两组 held-out seed 封装并未作为一个可直接启动的公开任务包给出**。因此：

- 环境安装和 development evaluator 可以直接复现；
- 要声称“严格复现 Arbor 最终 Test 分数”，还必须固定或向作者取得论文使用的 held-out seeds、seed 注入方式、失败处理和最终聚合脚本；
- 若团队自行建立两组隐藏 seeds，应标记为“Arbor-style reconstruction”，不要标记为论文 bit-for-bit 复现。

---

## 3. modded-NanoGPT（Track 3 Optimizer Design）：安装与运行

### 链接

- [modded-NanoGPT 官方仓库](https://github.com/KellerJordan/modded-nanogpt)
- [Track 3：Optimization Benchmark](https://github.com/KellerJordan/modded-nanogpt/tree/master/records/track_3_optimization)
- [Track 3 当前训练脚本](https://github.com/KellerJordan/modded-nanogpt/blob/master/records/track_3_optimization/train_gpt_simple.py)
- [Arbor 论文的 Optimizer Design 协议](https://arxiv.org/abs/2606.11926)

### 首先固定我们采用的协议

本项目要跑的是 **Arbor 论文协议**，而不是 2026 年 8 月当前 Track 3 `master` 的最新 leaderboard 协议。

| 项目 | Arbor 论文协议 | 当前公开 Track 3 `master` |
| --- | --- | --- |
| 初始 Muon baseline | 3325 steps 达到 `val_loss ≤ 3.28` | README 当前把 result #36、3250 steps 作为 tuned baseline |
| 分数 | 首次达到 `val_loss ≤ 3.28` 的 step count | 当前还加入统计显著性要求 |
| 停止方式 | 论文中的 `run_eval.py` 在首次达标时停止 | 当前规则不允许基于单次 val loss 做逐次 cherry-picked early stopping |
| 最终复测 | 2 个 held-out random seeds 取平均 | 当前公开 leaderboard 有自己的多次运行与显著性规则 |
| 硬件 | 4×A100-80GB | README 支持 1/2/4/8×A100 或 H100，但结果不能自动与论文相等 |

**因此，直接 clone 当前 `master` 并运行 README 命令，只能证明 Track 3 环境可运行，不能直接复现 Arbor 表中的 3325/3225/3237.5。**

### 当前上游环境的冒烟安装

```bash
git clone https://github.com/KellerJordan/modded-nanogpt.git
cd modded-nanogpt

# 冒烟测试可以锁定当前 commit；论文复现则必须换成论文时期的 commit/workspace
git checkout <PINNED_MODDED_NANOGPT_COMMIT>
git rev-parse HEAD

python -m venv .venv
source .venv/bin/activate

pip install torch==2.11 huggingface_hub

# 下载 2B FineWeb tokens；官方 README 说明足够约 4000 steps
python data/cached_fineweb10B.py 20

# 当前 Track 3 baseline 冒烟测试；服务器有 4 张卡时显式写 4
CUDA_VISIBLE_DEVICES=0,1,2,3 \
torchrun --standalone --nproc_per_node=4 \
  records/track_3_optimization/train_gpt_simple.py \
  | tee track3_smoke.log
```

### 我们正式跑什么

我们正式运行的是 **Optimizer Design AO**：

- 外层研究 Agent 获得论文时期的 tuned Muon baseline `train_gpt_simple.py`。
- 只能修改优化算法及其超参数；必须固定数据集、batch size、模型架构和训练任务。
- 每个 training step 最多一次 forward-backward pass。
- development evaluator 使用论文中的固定 `run_eval.py`，监控 FineWeb validation loss；首次达到 `val_loss ≤ 3.28` 的 step count 为分数，越低越好；未达标时使用论文规定的 >7000 penalty。
- 论文同条件硬件是 **4×A100-80GB**，外层搜索预算是 **48 小时**。
- 搜索完成后冻结最终优化器，在 **2 个 held-out random seeds** 上重新评测并取平均。

### 明确不跑什么

- 不跑仓库根目录的 NanoGPT 主 speedrun 作为本项目的 Optimizer Design 分数。
- 不以训练 wall-clock 秒数作为分数。
- 不允许 Agent 改 Transformer 架构、训练数据、batch size 或把一次 step 变成多次 forward-backward。
- H100 上可以完成代码适配、冒烟测试和本项目内部的同卡比较，但 H100 结果不能直接与 Arbor 的 4×A100-80GB 表格分数混用。

### 严格复现前必须取得或固定的资产

当前公开 Track 3 目录没有直接提供 Arbor 论文描述的完整 AO workspace。正式宣称论文复现前，需要锁定：

1. 论文使用的 **paper-time modded-NanoGPT commit** 和 3325-step tuned Muon 脚本；
2. 论文所称的官方 `run_eval.py`；
3. development seed、2 个 held-out seeds 及 seed 注入方式；
4. 4×A100-80GB 软件栈，包括 CUDA、PyTorch、NCCL 与容器镜像；
5. 失败、OOM、超时和未达标 penalty 的解析逻辑。

如果拿不到这些资产，可以基于论文描述重建 evaluator，但报告中必须写成 **“Arbor-style NanoGPT Track 3 reconstruction”**，不能写成精确官方 Adapter。

---

## 4. Terminal-Bench：安装与 `terminus-2` Harness Engineering AO

### 链接

- [Terminal-Bench 2.0 官方任务仓库](https://github.com/harbor-framework/terminal-bench-2)
- [Terminal-Bench 2.0 页面](https://www.tbench.ai/benchmarks/terminal-bench-2)
- [Harbor 官方仓库与 evaluator](https://github.com/harbor-framework/harbor)
- [`terminus-2` 官方源码目录](https://github.com/harbor-framework/harbor/tree/main/src/harbor/agents/terminus_2)
- [Arbor 论文的 Terminal-Bench 2.0 AO 协议](https://arxiv.org/abs/2606.11926)

### 先做 Harbor 与 Docker 冒烟测试

仅验证任务镜像、Docker 和 Harbor 能否工作时，可安装发行版：

```bash
uv tool install harbor
# 或：pip install harbor

harbor datasets list

# Oracle 冒烟：验证任务、容器和 verifier，不消耗 LLM API
harbor run --dataset terminal-bench@2.0 \
  --agent oracle \
  --n-concurrent 4
```

验证公开 `terminus-2` 能启动：

```bash
export OPENAI_API_KEY=<YOUR_KEY>

harbor run --dataset terminal-bench@2.0 \
  --agent terminus-2 \
  --model <PROVIDER/GPT-5.5-MODEL-ID> \
  --n-concurrent 1
```

上面的命令只是**环境冒烟**，运行的是公开完整 dataset 入口，并不是 Arbor 的 36-dev/53-test AO 协议。

### 正式 AO 必须从源码可编辑安装

因为外层 Agent 要修改 `terminus-2` 本身，不能只安装一个不可追踪的最新 wheel：

```bash
# 锁定 Harbor evaluator 与 terminus-2 源码
git clone https://github.com/harbor-framework/harbor.git
cd harbor

git checkout <PINNED_HARBOR_COMMIT>
git rev-parse HEAD

python -m venv .venv
source .venv/bin/activate
pip install -e .

harbor --help
harbor datasets list

# 另行锁定 Terminal-Bench 2.0 任务定义；不要让外层 Agent 修改它
cd ..
git clone https://github.com/harbor-framework/terminal-bench-2.git
cd terminal-bench-2
git checkout <PINNED_TERMINAL_BENCH_2_COMMIT>
git rev-parse HEAD
```

正式 workspace 中，外层 Agent 的可编辑范围应限制在：

```text
src/harbor/agents/terminus_2/
├── terminus_2.py
├── terminus_json_plain_parser.py
├── terminus_xml_plain_parser.py
├── tmux_session.py
├── templates/
└── 允许新增的 agent/prompt 辅助文件
```

Harbor evaluator、任务数据、API 配置、dev/test split 和 baseline reference record 必须只读。

### 我们正式跑什么

本项目的 Terminal-Bench 实验是：

```text
外层研究 Agent（EAR / Arbor / Codex / Claude Code / 其他已适配 Agent）
                       │
                       ▼
       修改并优化官方 terminus-2 harness
                       │
                       ▼
固定内层解题系统：修改后的 terminus-2 + GPT-5.5
                       │
                       ▼
  Harbor 在 36 dev tasks 上给搜索反馈；最终在 53 test tasks 上评分
```

正式协议：

- 初始产物是**未经修改的官方 `terminus-2` codebase**。
- 内层 backbone 对所有外层方法都固定为 GPT-5.5；外层研究 Agent 的模型可以不同，但必须与报告中设定一致。
- 89 道 Terminal-Bench 2.0 任务按难度分层，固定为 **36 dev + 53 held-out test**。
- 外层 Agent 在 **48 小时**内只能看和运行 dev evaluator。
- dev/test 均使用 Harbor，`HARBOR_N_CONCURRENT=8`。
- 搜索结束后冻结最终 `terminus-2`，每次独立外层搜索只对 53-task test set 做一次最终评测。
- 指标是 pass rate。

论文给出的正式 wrapper 命令是：

```bash
# 开发集：外层 Agent 在搜索期间可以反复调用
HARBOR_N_CONCURRENT=8 \
python3 run_eval.py --data data/dev.json

# 测试集：搜索结束、产物冻结后才可调用
HARBOR_N_CONCURRENT=8 \
python3 run_eval.py --data data/test.json
```

### 可以修改与禁止修改的内容

**可以修改：** `terminus-2` system prompt、每题附加指令、agent subclass、base agent/ReAct loop、response parser、tmux/terminal session management，以及 `agent`、`prompts` 范围内的新文件。

**禁止修改：** Harbor/evaluation harness、dev/test 数据、任务 verifier、API 配置、模型固定设置和 baseline reference record。外层 Agent 不能读取 test task IDs、test 日志或 test reward 来选方向。

### 绝对不要把下面的实验当成 Arbor AO

```bash
# 这些是“Agent 直接解 Terminal-Bench”的官方 Harbor 用法，
# 不是“外层 Agent 优化 terminus-2”的 AO 任务。
harbor run --dataset terminal-bench@2.0 --agent codex ...
harbor run --dataset terminal-bench@2.0 --agent claude-code ...
```

它们可用于确认 Codex/Claude Code adapter 在 Harbor 中能启动，也可形成另一组“直接解题”实验，但不能与 Arbor AO 的 77.36% 放在同一列直接比较。

### 严格复现前的阻塞项

Arbor 论文公开了 36/53 数量、难度分层原则、baseline、允许修改范围、8 workers 和指标，但公开 Arbor/Harbor 仓库没有直接给出一键启动的完整 AO 包。严格复现还需要：

1. 精确的 `data/dev.json` 与 `data/test.json` 任务 ID；
2. 论文使用的 `run_eval.py`；
3. paper-time Harbor/`terminus-2` commit；
4. GPT-5.5 的精确 provider model ID、推理配置、超时、最大 token 和重试策略；
5. 失败任务、API error 与重试的计分规则。

优先向 Arbor 作者取得这些资产。若团队自行按难度重建 36/53 split，必须固定 task ID 并标记为 **“Arbor-style Terminal-Bench AO reconstruction”**，不能声称与论文 split 完全相同。

---

## 5. FML-Bench：安装与运行

### 链接

- [FML-Bench 官方仓库](https://github.com/qrzou/FML-bench)
- [FML-Bench 论文](https://arxiv.org/abs/2605.17373)
- [Agent 配置目录](https://github.com/qrzou/FML-bench/tree/main/configs/agents)
- [Task 配置目录](https://github.com/qrzou/FML-bench/tree/main/configs/tasks)

### 安装

```bash
git clone https://github.com/qrzou/FML-bench.git
cd FML-bench

git checkout <PINNED_FML_BENCH_COMMIT>
git rev-parse HEAD

# 先看官方注册的任务名
python setup.py --list

# 完整安装 18 个任务、数据和 conda 环境
python setup.py

# 只做一个任务的安装冒烟时可用：
# python setup.py --task Causality_causalml

conda activate fmlbench
```

### 单任务冒烟

```bash
export OPENAI_API_KEY=<YOUR_KEY>
export CUDA_VISIBLE_DEVICES=0

python run_agent_benchmark.py \
  --agent-config configs/agents/ai_scientist_v2.yaml \
  --task-config configs/tasks/causality_causalml.yaml \
  --model gpt-5.4 \
  --provider OpenAI \
  --output-dir results \
  agent.ai_scientist_v2.max_steps=100
```

### 我们正式跑什么

- 为了与本文前面 FML 表格的完整结果对应，应运行 **全部 18 个 task configs**。
- 每个选定 Agent 使用相同模型、provider、GPU、max steps、初始代码、数据和 evaluator。
- 每个任务输出完整 step trajectory、token/cost、最终代码、`summary.json` 与 task metric。
- 同一个 Agent 完成 18 个任务后统一评分：

```bash
conda activate fmlbench
python compute_agent_metrics.py results/<agent_name>
```

- FML-Bench-Lite 只有 8 个任务，可用于 Adapter 调试、快速消融和成本受限实验；其结果应明确标为 Lite，不能写成完整 18-task 平均。

### Agent 适配要求

FML-Bench 不是“在命令中随便把 `--agent-config` 改成一个不存在的名字”就能运行。EAR、MLEvolve、Arbor、Codex、Claude Code、ML-Master 2.0 和 AweAI AiScientist 若不在当前 `configs/agents/` 和 `agents/` 实现中，必须分别实现：

- Agent class/runner；
- YAML config schema；
- workspace 初始化和代码可修改范围；
- step budget 与终止条件；
- 模型调用、token/cost 记录；
- 结果解析和异常处理。

只有完成注册并通过一个 task 的 end-to-end 冒烟后，才能进入 18-task 正式 sweep。

---

# Baseline Agent：仓库链接、安装与用途

## 计划纳入本项目的 Baseline Agent

| Baseline Agent | 官方/项目链接 | 原生最适合的入口 | 在其他 Benchmark 上的处理 |
| --- | --- | --- | --- |
| **Efficient Agent Research（EAR）** | 内部仓库：`<EAR_INTERNAL_REPO_URL>` | 本项目自身 Agent | 在报告定稿前替换为真实内部 Git URL，并记录 commit、启动命令和配置 |
| **MLEvolve** | [GitHub](https://github.com/InternScience/MLEvolve) | MLE-Bench/Kaggle 风格任务 | Autoresearch、NanoGPT、Terminal AO、FML 均需专用 Adapter |
| **Arbor** | [GitHub](https://github.com/RUC-NLPIR/Arbor) | 通用 AO；MLE plugin | 三个 AO 任务仍需各自 workspace/evaluator；论文任务包不等于当前仓库一键可用 |
| **Codex CLI** | [GitHub](https://github.com/openai/codex)；[官方文档](https://developers.openai.com/codex/cli) | 通用代码仓库操作 | MLE 需 runner；三个 AO 任务需权限、预算和 evaluator supervisor；FML 需 Agent Adapter |
| **Claude Code** | [GitHub](https://github.com/anthropics/claude-code)；[官方 Quickstart](https://docs.anthropic.com/en/docs/claude-code/quickstart) | 通用代码仓库操作 | 与 Codex 相同；正式运行要锁版本并禁止测试集访问 |
| **ML-Master 2.0** | [EvoMaster GitHub](https://github.com/sjtu-sai-agents/EvoMaster)；[ML-Master 2.0 目录](https://github.com/sjtu-sai-agents/EvoMaster/tree/main/playground/ml_master_2) | MLE-Bench/Kaggle 风格任务 | 其余四个 Benchmark 需要新的 task/workspace Adapter |
| **AiScientist（AweAI 系统）** | [GitHub](https://github.com/AweAI-Team/AiScientist) | MLE-Bench 与 PaperBench | 不等于 FML 中 The AI Scientist v1/v2；其余 Benchmark 需单独 Adapter |

## 1. Efficient Agent Research（EAR）

EAR 是项目内部系统，当前公开材料中没有可引用的外部仓库 URL。报告模板保留以下位置，正式实验前必须替换：

```bash
git clone <EAR_INTERNAL_REPO_URL>
cd <EAR_REPO_NAME>
git checkout <PINNED_EAR_COMMIT>

# 使用项目实际安装命令
<EAR_INSTALL_COMMAND>

# 记录实际统一入口
<EAR_RUN_COMMAND> --config <CONFIG> --workspace <BENCHMARK_WORKSPACE>
```

必须在报告中补齐：仓库 URL、commit、Python/容器环境、模型配置、统一启动入口、resume 行为、token/cost 统计方式和每个 Benchmark Adapter 的路径。

## 2. MLEvolve

### 链接

- [MLEvolve GitHub](https://github.com/InternScience/MLEvolve)
- [MLE-Bench GitHub](https://github.com/openai/mle-bench)

### 安装与原生运行

```bash
git clone https://github.com/InternScience/MLEvolve.git
cd MLEvolve
git checkout <PINNED_MLEVOLVE_COMMIT>

python -m venv .venv
source .venv/bin/activate

pip install --no-deps -r requirements_base.txt
pip install --no-deps -r requirements_ml.txt
pip install --no-deps -r requirements_domain.txt

# 按官方 README 修改 config/config.yaml：
# dataset_dir、base_url、api_key、模型和 time_limit 等

bash run_single_task.sh <COMPETITION_ID> <MLE_BENCH_DATA_DIR> [SERVER_ID]
```

MLEvolve 的上述入口是 MLE/Kaggle 原生入口。把它用于 Autoresearch、NanoGPT、Terminal AO 或 FML 时，必须让其搜索引擎操作对应可编辑 artifact，并把原来的 Kaggle submission evaluator 替换为目标 Benchmark evaluator。

## 3. Arbor

### 链接

- [Arbor GitHub](https://github.com/RUC-NLPIR/Arbor)
- [Arbor 中文 README](https://github.com/RUC-NLPIR/Arbor/blob/main/README.zh-CN.md)
- [Arbor 论文](https://arxiv.org/abs/2606.11926)

### 推荐从源码锁定安装

```bash
git clone https://github.com/RUC-NLPIR/Arbor.git
cd Arbor
git checkout <PINNED_ARBOR_COMMIT>

python -m venv .venv
source .venv/bin/activate
pip install -e .

arbor doctor
arbor setup
```

只需快速验证当前发行版时可用：

```bash
pip install arbor-agent
arbor doctor
```

正式实验不要只写 `pip install arbor-agent` 而不记录版本，因为 Arbor 是持续更新项目。每个 Benchmark workspace 至少需要固定 `run_eval.py`、dev/test 数据、只读文件范围和干净 Git 仓库。典型入口：

```bash
arbor --cwd /abs/path/to/benchmark_workspace \
  --config /abs/path/to/research_config.yaml
```

Arbor 论文运行过三个 AO 任务，不表示当前公开仓库已包含论文的精确 NanoGPT/Terminal dev-test 数据和 evaluator 包；这些复现资产仍应单独锁定。

## 4. Codex CLI

### 链接

- [Codex CLI GitHub](https://github.com/openai/codex)
- [Codex CLI 官方文档](https://developers.openai.com/codex/cli)

### 安装

```bash
# Linux/macOS 官方安装器
curl -fsSL https://chatgpt.com/codex/install.sh | sh

# 也可以通过 npm 安装，并显式锁版本
# npm install -g @openai/codex@<PINNED_VERSION>

codex --version
cd /abs/path/to/benchmark_workspace
codex
```

论文级比较必须保存 Codex CLI 版本、模型 ID、reasoning 配置、权限设置、初始 prompt、自动续跑方式和会话日志。Arbor 论文描述使用官方 `/goal` 长时运行模式，但论文没有在任务附录中给出足以从当前 latest CLI 恢复的精确 CLI build；因此应锁定团队验证过的版本，而不是在不同实验日自动升级。

Codex 在三个 AO 任务中的身份是**外层研究 Agent**：它修改 `train.py`、optimizer 脚本或 `terminus-2`；Terminal AO 中不是让 Codex 自己作为 Harbor `--agent codex` 去解题。

## 5. Claude Code

### 链接

- [Claude Code GitHub](https://github.com/anthropics/claude-code)
- [Claude Code 官方 Quickstart](https://docs.anthropic.com/en/docs/claude-code/quickstart)
- [Claude Code CLI Reference](https://docs.anthropic.com/en/docs/claude-code/cli-reference)

### 安装

```bash
# Linux/macOS/WSL 官方 native installer
curl -fsSL https://claude.ai/install.sh | bash

claude --version
cd /abs/path/to/benchmark_workspace
claude
```

正式 sweep 期间不得在不同 Agent/run 之间自动升级。保存 Claude Code 版本、模型全名、权限设置、初始 prompt、会话 ID、resume 策略、token/cost 和工具调用日志。

Claude Code 在 Autoresearch、NanoGPT 和 Terminal AO 中也是**外层研究 Agent**。尤其在 Terminal AO 中，它修改 `terminus-2`，不能把 `harbor run --agent claude-code` 的直接解题结果当作论文 Harness Engineering 结果。

## 6. ML-Master 2.0（EvoMaster）

### 链接

- [EvoMaster GitHub](https://github.com/sjtu-sai-agents/EvoMaster)
- [ML-Master 2.0 目录](https://github.com/sjtu-sai-agents/EvoMaster/tree/main/playground/ml_master_2)

### 安装与原生运行

```bash
git clone -b main --single-branch https://github.com/sjtu-sai-agents/EvoMaster.git
cd EvoMaster
git checkout <PINNED_EVOMASTER_COMMIT>

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
pip install -r playground/ml_master_2/requirements.txt

cp .env.template .env
# 在 .env 或 configs/ml_master_2/*.yaml 中填写模型端点和 API key

python run.py \
  --agent ml_master_2 \
  --config configs/ml_master_2/deepseek-v3.2-example.yaml \
  --task playground/ml_master_2/data/detecting-insults-in-social-commentary/prepared/public/description.md
```

该入口原生面向 MLE/Kaggle 任务。将它接到三个 AO Benchmark 时，需要把任务描述、可编辑 workspace、实验执行器、metric parser、预算监督器和最终 artifact 导出全部重写为对应 Adapter。

## 7. AiScientist（AweAI 系统）

### 链接

- [AweAI AiScientist GitHub](https://github.com/AweAI-Team/AiScientist)
- [MLE-Bench GitHub](https://github.com/openai/mle-bench)

### 安装与 MLE 原生运行

```bash
git clone https://github.com/AweAI-Team/AiScientist.git
cd AiScientist
git checkout <PINNED_AISCIENTIST_COMMIT>

cp .env.example .env
# 填写 OpenAI 或 Azure OpenAI 凭证
uv sync --dev

bash docker/build_mle_image.sh
uv run aisci mle doctor

uv run aisci --env-file .env mle run \
  --zip /abs/path/to/competition.zip \
  --name <competition-slug> \
  --image aisci-mle:test \
  --llm-profile <PINNED_LLM_PROFILE> \
  --gpu-ids 0 \
  --time-limit 12h \
  --wait \
  --tui
```

这里的 AiScientist 是 Arbor 表中引用的 **AweAI 系统**。FML-Bench 表中的 “The AI Scientist v1/v2” 是另一套 Agent 实现；二者不能只因为名字相似而当作同一 Baseline。

---

## 分数表中其他参考 Baseline 的链接索引

这些系统出现在前面的公开结果表中，但不等于本项目已决定全部重新运行。若后续纳入正式 Baseline，应再补充各自 commit、安装命令和 Adapter 状态。

| Agent | 链接 |
| --- | --- |
| AIDE | [https://github.com/WecoAI/aideml](https://github.com/WecoAI/aideml) |
| AIRA-dojo / AIRA MCTS / AIDE* Greedy | [https://github.com/facebookresearch/aira-dojo](https://github.com/facebookresearch/aira-dojo) |
| R&D-Agent | [https://github.com/microsoft/RD-Agent](https://github.com/microsoft/RD-Agent) |
| MARS / MARS+ | [https://github.com/jfc43/MARS](https://github.com/jfc43/MARS) |
| AIBuildAI | [https://github.com/aibuildai/AI-Build-AI](https://github.com/aibuildai/AI-Build-AI) |
| Famou-Agent 2.0 | [https://github.com/baidubce/FM-Agent](https://github.com/baidubce/FM-Agent) |
| NexAU-AHE | [https://github.com/china-qijizhifeng/agentic-harness-engineering](https://github.com/china-qijizhifeng/agentic-harness-engineering) |
| Terminus 2 | [Harbor `terminus-2` 源码](https://github.com/harbor-framework/harbor/tree/main/src/harbor/agents/terminus_2) |
| OpenHands | [https://github.com/OpenHands/OpenHands](https://github.com/OpenHands/OpenHands) |
| Mini-SWE-Agent | [https://github.com/SWE-agent/mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) |
| FML-Bench 内置 The AI Scientist v1/v2、AIDE、AIRA、Autoresearch、OpenEvolve、AdaptiveSearch | [FML `configs/agents`](https://github.com/qrzou/FML-bench/tree/main/configs/agents) |

---

# 统一实验记录与验收清单

每个 Benchmark × Agent × seed/run 都必须记录以下内容，否则分数不进入主表：

- Benchmark 仓库 URL、commit/tag、未提交 diff；
- Baseline Agent 仓库 URL、commit 或 CLI 精确版本；
- 模型 provider、完整 model ID、reasoning/temperature 等配置；
- GPU 型号、数量、驱动、CUDA、PyTorch、Docker/容器 digest；
- 外层 wall-clock/step/token/API 预算及超时规则；
- 可编辑文件 allowlist 和只读文件 denylist；
- dev/test split 的 hash，且搜索阶段无法访问 test；
- 初始 baseline 分数能够在同一环境中复现；
- 最终 artifact commit/hash、原始日志、evaluator 输出和失败记录；
- 外层独立重复次数与最终 held-out seed 次数分开统计；
- 只有 Benchmark、模型、硬件、预算、数据和 evaluator 完全相同的结果才放在同一可直接比较的列中。

## 部署顺序建议

1. 先完成每个 Benchmark 的官方/上游冒烟测试；
2. 再固定论文协议所需 commit、数据 split 和 evaluator；
3. 先接 EAR 与一个最简单 Baseline，验证 Adapter 的公平性；
4. 用未修改初始 artifact 复现 baseline 分数；
5. 才开始 48 小时或完整 18-task 正式实验；
6. 测试集只在协议规定的最终阶段调用，并保存只读审计日志。


benchmark适配情况（原本）

| Agent | MLE-Bench Lite | Autoresearch：Architecture Design | modded-NanoGPT：Optimizer Design | Terminal-Bench：Arbor AO | FML-Bench 当前版 |
| --- | --- | --- | --- | --- | --- |
| **MLEvolve** | **✅**** Agent 官方原生适配**。项目直接面向 MLE-Bench，官方榜单 Lite 成绩为 80.30%。 | **— 未发现**。现有入口围绕 Kaggle/MLE-Bench 数据集、submission 和竞赛评分组织。 | **🛠**** 只有方法层接近**。MLEvolve 做过数学算法优化，但没有公开的 modded-NanoGPT Track 3 Adapter。 | **— 未发现**。没有 terminus-2 优化或 Harbor AO 封装。 | **— 未发现当前适配**。不在当前 FML-Bench 注册 Agent 列表中。 |
| **Arbor** | **✅**** 官方适配**。公开了 `mle_kaggle`<br/> 插件及 `mle_bench_lite`<br/> profile；论文也报告了结果。 | **🟨**** 官方论文级半适配**。论文完整跑过，但精确 workspace、隐藏种子评测器和任务包未在公开 `arbor-zoo`<br/> 中发布。 | **🟨**** 官方论文级半适配**。论文跑的是 modded-NanoGPT Track 3，但没有找到公开可直接启动的任务包。 | **🟨**** 官方论文级半适配**。论文完成了 terminus-2 的 36 个开发任务、53 个隐藏任务优化实验，但对应 AO workspace 未公开打包。 | **🛠**** 通用接口可接，无专用 Adapter**。Arbor 的 YAML Plugin 可以封装任意 evaluator，但未发现 FML-Bench plugin/config。 |
| **Codex** | **🟦🟨**** Arbor 第三方跑过，Adapter 未公开**。Arbor 论文报告 Codex GPT-5.5 的 Lite 成绩 68.18%，但没有公开独立 Codex-MLE runner。 | **🟦🟨**** 第三方半适配**。Arbor 论文跑过；原始 Autoresearch 仓库也允许直接让 Codex 按 `program.md`<br/> 修改 `train.py`<br/>，但 Arbor 的隐藏种子封装未公开。 | **🟦🟨**** 第三方半适配**。Arbor 论文跑过，未发现公开的预算监督器和 held-out evaluator Adapter。 | **🟦🟨**** Arbor AO 跑过**；此外 Harbor 对“直接解 Terminal-Bench 任务”有 **✅**** Benchmark 官方 Codex CLI Adapter**。但两者不是同一个任务。 | **— 当前无适配**。当前 FML-Bench 注册列表没有 Codex。 |
| **Claude Code** | **🛠**** Benchmark 可接，但无专用公开 Adapter**。MLE-Bench 本身是 Agent-agnostic，但未找到 Claude Code 对 Lite 的正式 runner 或 Arbor论文结果。 | **🟦🟨**** 第三方半适配**。Arbor 论文跑过；原始 Autoresearch 也明确支持直接让 Claude/Codex 操作仓库，但 Arbor held-out 封装未公开。 | **🟦🟨**** 第三方半适配**。Arbor 论文跑过，执行封装未公开。 | **🟦🟨**** Arbor AO 跑过**；Harbor 同时提供 **✅**** 原生 Claude Code Adapter**，但它用于直接解 Terminal-Bench，不是优化 terminus-2。 | **🟪**** 仅 Legacy 官方适配**。2025 旧版 FML-Bench 专门设计过 Claude Code prompting scheme；2026 当前主分支注册列表已不包含 Claude Code。 |
| **ML-Master 2.0** | **✅**** Agent 官方原生适配**。主要评测目标就是 MLE-Bench；Arbor 表中引用 Lite 成绩 75.76%。 | **🛠**** 只有框架级可移植性**。EvoMaster 支持下游任务扩展，但没有公开 Autoresearch Adapter。 | **🛠**** 只有框架级可移植性**。没有 Track 3 workspace、metric parser 或 optimizer-specific 配置。 | **— 未发现**。没有 Terminal-Bench 或 terminus-2 优化入口。 | **— 未发现当前适配**。不在当前注册列表中。 |
| **AiScientist（Arbor 表中的 AweAI 系统）** | **✅**** Agent 官方适配**。官方仓库包含 MLE-Bench integration，论文报告 Lite 81.82%。 | **— 未发现**。官方公开 benchmark integration 只有 PaperBench 与 MLE-Bench。 | **— 未发现**。没有 modded-NanoGPT Track 3 Adapter。 | **— 未发现**。没有 Terminal-Bench/Harbor Adapter。 | **— 精确 Agent 无适配**。FML-Bench 里的 “The AI Scientist v1/v2” 是另一套 Agent，不是 Arbor 表中引用的 AweAI AiScientist。 |




部署情况

| Benchmark | 部署位置 | 部署情况 |
| --- | --- | --- |
| MLEBenchLite | 4090 | MLEvolve和EAR测试已经通过 |
| TerminalBench | 4090 | <font style="background-color:#FBDE28;">等待决定baseline数量后适配</font> |
| AutoResearch | H100 | <font style="background-color:#FBDE28;">等待决定baseline数量后适配</font> |
| NanoGPT Optimizer Design | H100 | <font style="background-color:#FBDE28;">等待决定baseline数量后适配</font> |
| FML-Bench | H100 | <font style="background-color:#FBDE28;">等待决定baseline数量后适配</font> |




Benchmark适配情况

| Agent | MLE-Bench Lite | Autoresearch：Architecture Design | modded-NanoGPT：Optimizer Design | Terminal-Bench：Arbor AO | FML-Bench 当前版 |
| --- | --- | --- | --- | --- | --- |
| **Efficient Agent Research** | **✅**已经适配 |  |  |  |  |
| **MLEvolve** | **✅**** Agent 官方原生适配**。项目直接面向 MLE-Bench，官方榜单 Lite 成绩为 80.30%。 |  | **** |  |  |
| **Arbor** | **✅**** 官方适配**。公开了 `mle_kaggle`<br/> 插件及 `mle_bench_lite`<br/> profile；论文也报告了结果。 |  |  |  |  |
| **Codex** |  |  |  |  |  |
| **Claude Code** |  |  |  |  |  |
| **ML-Master 2.0** | **✅**** Agent 官方原生适配**。主要评测目标就是 MLE-Bench；Arbor 表中引用 Lite 成绩 75.76%。 |  |  |  |  |
| **AiScientist（Arbor 表中的 AweAI 系统）** | **✅**** Agent 官方适配**。官方仓库包含 MLE-Bench integration，论文报告 Lite 81.82%。 |  |  |  |  |




## 