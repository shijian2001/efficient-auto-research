# 二、环境搭建与 Agent 配置

> 涵盖：目录结构、mlebench 轻量安装、数据准备、LLM 接入架构（转发代理）、活跃 Agent 的配置与运行。
>
> **2026-07 重构说明**：早期版本曾对各 agent 做侵入式修改（Bedrock Converse 适配、token 记录、
> 重试等，改动散落 10+ 文件）。现已全部废弃——agent 回到纯上游，LLM 适配集中到
> `docker-eval/llm_relay_proxy.py`。历史修改如需考古：MLEvolve 看 `gpt55-local` 分支，
> 本文档旧版看 git history。
>
> **当前更新（2026-07-22）**：EAR 正式候选已切到 `ear/g5`。G5 的 run/attempt
> 隔离、manifest/report/hash 契约见[文档七](07_g4_failure_and_g5_infrastructure.md)。

---

## 1. 硬件与目录

| 资源 | 规格 |
|------|------|
| GPU | 8× RTX 4090 (24GB each) |
| CPU / RAM | 256 vCPU / 251 GB |
| CUDA | 12.4 |
| Python | 3.11 (Miniconda) |

**统一目录** `/mnt/sdc/shijianwang/efficient-agent-research/`（总览见根 README）。

**环境激活：**
```bash
export PATH="/mnt/sdc/shijianwang/miniconda3/bin:$PATH"
source /mnt/sdc/shijianwang/miniconda3/etc/profile.d/conda.sh
conda activate mlebench
```

---

## 2. mlebench 框架轻量安装

`pyproject.toml` 声明了 tensorflow(1.9GB)、docker、pymongo 等重依赖，但选定 6 题用不到。

```bash
# 1. 注册包，跳过所有声明依赖
pip install --no-deps -e /mnt/sdc/shijianwang/efficient-agent-research/mle-bench
# 2. 只装 6 题评分需要的轻依赖
pip install appdirs pandas numpy scikit-learn scipy pyyaml py7zr diskcache tenacity tqdm kaggle
```

完整安装 ~2GB+/10-30min；轻量安装 ~200MB/<2min，评测能力等价。

> PyTorch（MLEvolve 的 FAISS embedding 需要）：
> `pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124`

---

## 3. 数据准备

```bash
# ~/.kaggle/kaggle.json 用旧版 username+key 格式（新版 access_token 与 kaggle 1.6 不兼容）
chmod 600 ~/.kaggle/kaggle.json
# 需先在网页接受各竞赛 rules，否则 403
kaggle competitions download -c <competition_id> -p <data_dir>/<competition_id>/
```

下载后用 mlebench `prepare_fn` 解压 + 分 public/private。6 题共 3.5 GB。

HF 模型（chaii 需要多语言 backbone）预下载在 `cache/huggingface`，容器可写挂载：
bge-base-en-v1.5（EAR embedding）、xlm-roberta-base/large(+squad2)、
bert-base-multilingual-cased、muril-base-cased。

---

## 4. LLM 接入：转发代理架构

所有 agent 通过 `OPENAI_BASE_URL` 指向容器内的本地代理，代理统一完成模型重写(→gpt-5.5)、
reasoning_effort 注入、参数清洗、重试、非流式化、tool-call 兜底和 token 记录。

**设计原则：agent 代码零侵入** —— 换模型/换端点/调参数只改代理或环境变量，
升级 agent 上游永不冲突。细节见 [docker-eval/README.md](../docker-eval/README.md) 第 2 节。

演进备忘：硅基流动 DeepSeek-V3.2（代码质量差）→ AWS Bedrock Claude Sonnet 4.6
（Converse API 需逐 agent 适配，维护成本高）→ **relay gpt-5.5 + 本地代理（现行）**。

---

## 5. 各 Agent 配置与运行

统一入口：`bash docker-eval/run_in_docker.sh <agent> <comp> <gpu> [steps] [timeout]`，
以下为脚本内部实际做的事。

### MLEvolve (#4) — `main` = 纯上游

- 方法：Monte Carlo Graph Search + UCT，stepwise 代码生成，LLM 结果解析 + format server 校验
- 配置全部经 hydra CLI 覆盖（`agent.code.model/base_url/api_key` 等），yaml 零修改
- 先起 grading server（端口 `5200+gpu`），再跑 `run.py`
- `exp_name=$COMP`：config 自动加时间戳前缀成三段式，满足 result_parse_agent
  `split('_')[2]` 取 exp_id 的约定
- 依赖补装：flask, rich, faiss-cpu, sentence-transformers, black

### Arbor — `baselines/Arbor`

- 当前活跃开源 baseline，保持原版代码，不纳入 EAR/MLEvolve 的历史对比结果表。

### efficient-auto-research（自研 KTS）— `ear/g5`

- 方法：Kernel Thompson Sampling。搜索树建模为 GP Regression（cosine kernel on
  plan+code embedding），精确后验 Thompson Sampling 选父节点；每步仅 2 次 LLM 调用
- G1–G3 包含从 12h 长跑归因反哺的算法/执行改进：
  执行前 preflight（拒绝 prose/diff/语法错误）、代码提取严格化 + 重试、
  submission 对照 sample 校验（列名/行数/内容）、best_submission 持久化、逐步 trace
- G4 (`7be8152`) 的 no-op/duplicate 搜索过滤因六题退步已撤销；G5 保持 G3 搜索资格
- G5 (`a6acc90`) 新增 per-run/per-attempt 隔离、bounded-memory validator、冻结 artifact、
  current-run final hash 绑定、host provenance、launch manifest 和只读 behavior telemetry
- 正式产物位于 `$EAR_AGENT_DIR/docker_runs/<tag>_<comp>/workspace/runs/<run_id>/`
- LLM 层为 ~70 行纯 OpenAI client，无任何 provider 特殊逻辑

### AIBuildAI (#2) — 未跑通，已移除

预编译二进制，内部经 subprocess 调 `claude` CLI（需交互式登录 + TTY），
非交互后台环境下空转烧 CPU（实测 99% × 7 天、日志 6GB），已清理。详见文档三。

---

## 5.5 对比实验公平性口径（2026-07-04 审计后确立）

`20260703_12h_proxy_rerun` 轮次后做了一次系统性公平审计，发现并修复了以下问题。
**当前口径：LLM 链路与硬件完全对称；各 agent 用自身发布默认配置；唯一例外是
MLEvolve 的 coldstart 被显式关闭（理由见 ③）。**

| # | 问题 | 处置 |
|---|------|------|
| ① | EAR prompt 里有 mlsp-2013-birds 题目专属提示（tabular 特征/OvR 线性模型等人类先验，2026-06-09 调试期遗留，随 159bb9e 误入 `proxy-based-eval` 分支）| ✅ 已删（EAR commit `fee7df0`）。**受此影响：20260703 轮 EAR mlsp 银牌 0.9331 含水分，需以后续轮次为准** |
| ② | launcher 对 MLEvolve 的省钱裁剪（06 月 API 调试期遗留）：initial_drafts 3→2、parallel_search 3→1、debug_depth 20→5、global_memory 关、exec.timeout 32400→1800，且漏跑官方 submission fusion 后处理 | ✅ 全部恢复上游默认 + 补 fusion（launcher 对齐官方 `run_single_task.sh`）。**受此影响：20260703 轮 MLEvolve 的分数是被削弱配置跑出的，同样需重跑** |
| ③ | **MLEvolve coldstart 知识库的来路问题**：`engine/coldstart/` 含两个文件——`models_guidance_classified.json`（5 类任务的通用预训练模型卡+代码模板，内容干净，属作者通用工程知识）和 `competition_tag_classified.json`（**MLE-bench 全部 75 题的竞赛→类别硬编码映射**）。后者是针对本 benchmark 的预计算适配："识别这是什么类型的题"本应是 agent 运行时读题的能力，被提前做成了查表。不泄露解法/标签，刷榜圈属灰色但常见操作，MLEvolve 发布的 65.3% 按其官方脚本推定带此配置 | ✅ 对照实验**默认关闭**（launcher `coldstart.use_coldstart=False`，`MLE_COLDSTART=1` 可复现官方形态）。对无知识库的 EAR 更公平；代价是我们跑的不是 MLEvolve 官方发布形态，报告须注明 |
| ④ | CPU 不对称：MLEvolve 自绑 8 核（三容器还踩同一批 0-7 核），EAR 放飞 256 核 | ✅ 所有容器 `--cpuset-cpus` 独占 21 核（= MLEvolve 官方 CPUS_PER_TASK），按 GPU_ID 错开 |
| ⑤ | 单步执行超时不对称：EAR 3600s vs MLEvolve 被压到 1800s | ✅ launcher 不再强加，各用自身默认（MLEvolve 32400 / EAR min(3600, timeout//3)） |

其他确认对称项：同模型 gpt-5.5 + 同 reasoning_effort=high（代理强制注入）、同代理重试/
参数清洗、同数据、同 12h 墙钟、同官方评分。

遗留提示：EAR 与 MLEvolve 恰好共用 `BAAI/bge-base-en-v1.5` 做各自记忆/kernel 的
embedding，是天然的控制变量；MLEvolve 官方成绩用 Gemini-3.1-Pro 跑出，与我们的
gpt-5.5 结果不可直接横比。

---

## 6. conda 迁移修复（备忘）

miniconda3 从 `/home` 迁到 `/mnt/sdc` 后需批量改硬编码路径：
`etc/profile.d/conda.sh` 的 4 个路径变量、`bin/` 与 `envs/*/bin/` 脚本 shebang、`condabin/conda`。
editable 包（mlebench）需重新 `pip install --no-deps -e <新路径>`。
