# 当前磁盘上的 Agent 版本（2026-08-27）

本轮实验**不追上游最新版**。各家用本机 checkout 里已经有的代码跑，
并把身份记在这里。未提交的补丁也是这套版本的一部分，不是要丢掉的脏文件。

记录日期：2026-08-27。外层仓库：`full4090`，远端
`git@github.com:shijian2001/efficient-auto-research.git`。
这一层**已经包含** `baselines/` 和 `mle-bench-agents/` 的源码快照；推 `full4090`
就是把 Baseline 推到你自己的仓库，不是推到 Arbor / AiScientist 官方。

各家目录里另外还有一份嵌套 `.git`（clone 时留下的）。`formal_source_clean`
查的是那一份。2026-08-27 已把当时工作区里的补丁做成**本地 commit**（不 `push` 到
RUC-NLPIR / AweAI-Team / InternScience / sjtu-sai-agents）。EAR 内层 `origin`
也指向你的 `shijian2001/efficient-auto-research`，分支是 `ear/g3`，和 `full4090` 不是同一条线。

## 进表身份（本地 pin 之后）

| Agent | 跑哪棵树 | HEAD（40 位） | 分支 | 工作区 | `--agent-variant` |
|---|---|---|---|---|---|
| EAR | `mle-bench-agents/efficient-auto-research` | `169947e667d0f555c1ca680cf24166fbe78e20be`（叠在 G3 `7cd9ed5` 上） | `ear/g3` | **干净** | `g3@7cd9ed5c1db0ff5250faad373e5d5a67209e604c` |
| MLEvolve | `baselines/MLEvolve` | `ed595138c62a3785532bfb11cbd14b14e51a701f` | `main` | **干净** | 原版 ID |
| Arbor（registry / AO 默认路径） | `baselines/Arbor` | `92c6fd5c22c8a291796d39730605ac0eb8ba07c5` | `feat/mle-bench-adapter-hardening` | **干净** | MLE 必须 `arbor-benchmark-patched` |
| Arbor（MLE docker 实际执行） | `baselines/Arbor-longrun-patched` | `a51a1fe4b48e07259a22a02d4969abc04494f77e` | `feat/mle-bench-adapter-hardening` | **干净** | 同上；12h 长跑用这棵树 |
| Codex | `baselines/Codex`（无内层 `.git`，跟外层走） | 外层 `f9f4ba674b034c54937d7325cb649b1d05ff501d` | 外层 `full4090` | 跟外层 | 原版 ID |
| Claude Code | `baselines/ClaudeCode`（无内层 `.git`，跟外层走） | 外层 `f9f4ba674b034c54937d7325cb649b1d05ff501d` | 外层 `full4090` | 跟外层 | 原版 ID |
| ML-Master 2.0 | `baselines/EvoMaster` | `07a80dac7f9edad18f2d97bcbffc0585e06d5b46` | `main` | **干净** | 原版 ID（AO 不进表） |
| AiScientist | `baselines/AiScientist` | `aae385b12b0d1e5ad928c6f988a769cfb173b3e7` | `main` | **干净** | AO 必须 `ai-scientist-terminal-variant` |

Codex / Claude Code 没有独立 nested git。`git -C baselines/Codex rev-parse HEAD`
会走到外层仓库，不要把它当成 Codex 上游 commit。Claude Code 树内 changelog 顶是
`2.1.222`，仅作磁盘标记，不是 npm 安装版本审计。

## 本地 pin 之后各家是什么

### EAR = G3，不升 G4–G7

本地 commit `169947e` 叠在 G3 `7cd9ed5` 上，内容是当时工作区里的 repo-mode 与 smoke RNG 隔离。
`build/` 已写入内层 `.gitignore`。CLI 的 `--agent-variant` 仍写 `g3@7cd9ed5…`（G3 代际标签）；
成绩单上的 `agent_commit` 会是 `169947e`。

### Arbor：修过 bug 的当前树 + 12h 长跑树

两棵树都保留。

- `baselines/Arbor` 本地 commit `92c6fd5`：当时工作区里的 MLE adapter bugfix。`cache/` 已忽略。
- `baselines/Arbor-longrun-patched` 仍是 `a51a1fe`（干净）。MLE docker 从这棵树快照。
- registry 的 `install_path` 仍是 `baselines/Arbor`；成绩单要同时写下两棵树的 commit。

### AiScientist

本地 HEAD `aae385b`：`770039a` 的 `TerminalTaskSubagent`（AO 的
`ai-scientist-terminal-variant`），加上 Dockerfile `ARG BASE_IMAGE` 和
MLE 域尊重 `AISCI_LLM_PROFILE_FILE`。工作区干净。

### MLEvolve / ML-Master 2.0

- MLEvolve `ed59513`：只提交了 `adapter_docs/`；`llm_token_usage.jsonl` 已忽略。
- EvoMaster `07a80da`：`llm.py` / `watch_dog.py` / `adapter_docs/` / `uv.lock`。工作区干净。AO 不进表。

### Codex / Claude Code

无 nested `.git`，身份跟外层 `full4090` @ `f9f4ba674b034c54937d7325cb649b1d05ff501d`。

## 比较集合（不变）

- MLE-Bench Lite：七家都进表。
- Terminal AO：EAR、Arbor、Codex、Claude Code、AiScientist。MLEvolve 与 ML-Master 2.0 不进表。

## Terminal AO benchmark 来源（2026-08-27 补）

AO protocol schema 2 需要 40 位 `benchmark_source_commit`。本机
`terminal-bench-2/` 没有 `.git`，该哈希按下述证据推定：

- `terminal-bench-2/config/dataset_source.toml` 记录 2026-08-01 用
  `harbor download terminal-bench@2.0` 下载，89 题。
- 上游 `harbor-framework/terminal-bench-2` 最后一次 push 是 2026-04-30，
  早于下载日，因此下载时 HEAD 稳定在
  `2fd12b88aafdd04a52c298e3940bcb189f9766d6`（"Add task metadata to task manifests"）。
- 该 commit 的 tree 与本地 `datasets/terminal-bench-2/` 的 89 个任务目录名
  **逐一比对完全一致**。

这是间接推定，不是从本地 git 直接读出的。若日后拿到直接记录与此不符，
应重新生成 protocol 并作废受影响的成绩。

## AiScientist 重新 pin（2026-08-27）

`770039a` → `61522b7`：给 `docker/mle-agent.Dockerfile` 补上
`ARG BASE_IMAGE`。上游的 `build_mle_image.sh` 一直在传这个 build-arg（默认公网
`ubuntu:24.04`），但 Dockerfile 没声明，参数失效，构建只能拉内网基础镜像。
补这一行之后可以用上游自己的脚本构建出 MLE 运行镜像。`UPSTREAM_REVISIONS`
已同步更新。补丁只在本机 pin，未回上游。

`61522b7` → `aae385b`：让 MLE 域尊重 `AISCI_LLM_PROFILE_FILE`。
`--llm-profile-file` 会导出这个变量，共享解析器读它，paper 域也因为不传
`profile_file` 而自然生效——只有 `domain_llm_profile_file()` 硬返回源码树里的
固定路径，而显式参数优先级高于环境变量，于是该 flag 在 MLE 路径上被静默忽略，
每个阶段都到上游自带的 `config/llm_profiles.yaml` 里找我们生成的 profile，
报 `Unknown LLM profile: benchmark-model`。同样只在本机 pin。
