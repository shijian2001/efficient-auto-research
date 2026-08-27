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
| AiScientist | `baselines/AiScientist` | `770039abc8f1319f436542b16f630d70d117d322` | `main` | **干净** | AO 必须 `ai-scientist-terminal-variant` |

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

本地 commit `770039a` 含 `TerminalTaskSubagent`（AO 的 `ai-scientist-terminal-variant`）。工作区干净。

### MLEvolve / ML-Master 2.0

- MLEvolve `ed59513`：只提交了 `adapter_docs/`；`llm_token_usage.jsonl` 已忽略。
- EvoMaster `07a80da`：`llm.py` / `watch_dog.py` / `adapter_docs/` / `uv.lock`。工作区干净。AO 不进表。

### Codex / Claude Code

无 nested `.git`，身份跟外层 `full4090` @ `f9f4ba674b034c54937d7325cb649b1d05ff501d`。

## 比较集合（不变）

- MLE-Bench Lite：七家都进表。
- Terminal AO：EAR、Arbor、Codex、Claude Code、AiScientist。MLEvolve 与 ML-Master 2.0 不进表。
