# MLE / Terminal AO 运行手册

本手册维护这台 4090 主机的运行步骤。当前能力与实验进度见
[Adapter README](../README.md)，源码 pin 见[版本记录](ON_DISK_AGENT_VERSIONS.md)。
本轮冻结配置为 `gpt-5.6-terra`、N=1、MLE 每题 12h / AO 外层 48h。

## 先区分暂停续跑与新建运行

2026-09-20 核验时，以下运行仍在暂停状态：

| 运行 | 应读取的本地记录 |
|---|---|
| `20260904_125112_mle_7agent_22task_remaining` 的三个 MLEvolve cell | `.runtime/campaign-pause-20260917T172424.json`；三个暂停容器及其原挂载 |
| `20260920_mle_five_sticky_v7`，两题 ML-Master + 三题 AiScientist 的计划 | `.runtime/campaign-pause-20260920.json`；campaign 内的 `controller.json`、`pause.json`、`plan.json`、`launcher-source.py` |

v7 当前入口是本机 `analysis/run_mle_five_sticky_20260920.py`，其副本保存在 campaign
中。它及运行目录受 `.gitignore` 影响，不会自动随 Git 分发。保留这些文件和原路径。
AiScientist 容器可能显示 Docker `running`，但其进程被 SIGSTOP；需要同时读暂停收据
和进程状态。零字节的 GPU/端口/controller 锁也可能正在被持有。

下面的 `mle-cell` 命令用于**新建运行**，不是恢复现有暂停 cell。恢复时以收据中的
PID、启动时间、容器 ID、原配置和预算为依据；wall-clock 在进程暂停期间仍会推进，
不能把直接重启 launcher 或重新跑一格当作保留原预算的 resume。本次文档整理没有
恢复这些实验。

正式入口检查 adapter 和 Agent 工作区是否干净，成绩还绑定 adapter commit。
文档修改也会使工作区 dirty；正在进行的 campaign 应保留其源码版本，在独立 worktree
整理文档或开发下一轮更改。

## 冻结资产

所有路径均相对项目根目录。

| 用途 | 路径与含义 |
|---|---|
| MLE 协议 | `BenchmarkAdapters/configs/mle-protocol.n1-12h.json`；22 题、seed `[0]`、43200 秒 |
| MLE 数据身份 | `BenchmarkAdapters/MLEBenchLite/data_manifest.json`；schema 2，绑定 prepared / archive / grader 资产 |
| AO 协议 | `terminal-bench-2/ao_protocol/protocol.json`；schema 2、36/53、seed `[0]`、172800 秒 |
| 默认 model-track | `BenchmarkAdapters/configs/model-track.gpt-5.6-terra-host-relay.json`；共享 host relay 6201 |
| v7 的实际 model-track | campaign 内 `model-track-slot-1.json` / `model-track-slot-2.json`；6202 / 6203，按任务固定 provider credential |

已有协议不为解决启动错误而重新生成。N=3 是独立的后续设计；本轮 N=1 只能报告
`single_run`，不能写 Avg@3 或显著差异。旧 gpt-5.5 track 及其结果保持独立。

## 新建运行所需的 host relay

共享默认配置使用 **127.0.0.1:6201**。Agent sandbox 内常见的 6200 是 per-run
入口，不能据此把共享配置改成 6200。v7 的 6202/6203 使用原保存配置，不应被共享
服务替换。协议和输出 token 上限的详细行为统一见 [LLM relay](../LLMRelay/README.md)。

在新运行的整个期间保持该服务存活；服务启动脚本读取 `~/.mle_relay_env`，其中需要
配置上游 URL 和 `UPSTREAM_API_KEY`。凭据不写入 model-track JSON。

```bash
cd /mnt/sdc/shijianwang/efficient-agent-research
bash docker-eval/start_mle_host_relay.sh
```

脚本固定共享服务的 model/temperature/reasoning effort，使用代理 17892，并将 token
日志默认写入 `cache/mle-host-relay-proxied.tokens.jsonl`。per-run relay 不会自动启动
这项共享服务。缺少容器镜像时的代理下载工具见 [Docker runner](../../docker-eval/README.md)。

host relay 的入站凭据必须与 per-run relay 实际使用的凭据一致。连接拒绝通常是端口
无人监听；401 是入站凭据不符，不能当作 Agent 解题失败。密钥池及粘性策略见
[RELAY_KEY_POOL.md](RELAY_KEY_POOL.md)。

## Agent 身份

正式入口要求显式 `--agent-variant`，拒绝 `default`。当前完整 commit 与本机状态
只在[版本记录](ON_DISK_AGENT_VERSIONS.md)维护。

| Agent | MLE variant | AO variant |
|---|---|---|
| EAR | `ear` 或版本记录中的 G3 标签 | 同左 |
| MLEvolve | `mlevolve` | 不参与 |
| Arbor | `arbor-benchmark-patched` | `arbor@<当前 pin>` |
| Codex | `codex` | `codex` |
| Claude Code | `claude-code` | `claude-code` |
| ML-Master 2.0 | `ml-master-2@<当前 pin>` | 不参与 |
| AiScientist | `ai-scientist@<当前 pin>` | `ai-scientist-terminal-variant` |

`codex-budget-loop` / `claude-code-budget-loop` 是单独登记的 MLE 续跑变体，不能与表中
原版单次 CLI 混用；已有运行按 manifest 中的实际 variant 继续处理。

需要带 commit 的值可从代码读取，避免继续复制 8 月的旧 ML-Master pin：

```bash
./BenchmarkAdapters/.venv/bin/python -c 'from BenchmarkAdapters.thin_registry import UPSTREAM_REVISIONS; print("\n".join(f"{agent}@{revision}" for agent, revision in UPSTREAM_REVISIONS.items()))'
```

## 新建 MLE cell

以下示例使用新目录，正式预算为 12h。先在运行 shell 中加载 relay 环境，再执行
formal-preflight。**formal-preflight 会向配置的 relay 发送真实模型请求**；`--help`
和参数解析才是离线检查。以 JSON 中所有适用检查为准，不固定写死检查数量。

```bash
set -a
source "${MLE_RELAY_ENV_FILE:-$HOME/.mle_relay_env}"
set +a

./BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters formal-preflight \
  --benchmark mle-bench-lite --agent codex --agent-variant codex \
  --protocol BenchmarkAdapters/configs/mle-protocol.n1-12h.json \
  --model-config BenchmarkAdapters/configs/model-track.gpt-5.6-terra-host-relay.json \
  --data-root mle-bench-data

./BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters mle-cell \
  --protocol BenchmarkAdapters/configs/mle-protocol.n1-12h.json \
  --agent codex --agent-variant codex \
  --competition-id spooky-author-identification --seed 0 \
  --data-root mle-bench-data --campaign-dir /runs/mle/new-campaign --gpu-id 0 \
  --model-config BenchmarkAdapters/configs/model-track.gpt-5.6-terra-host-relay.json
```

`mle-cell` 保留已有 manifest/result，不能靠复用同一个目录覆盖旧失败。实际批量
controller 的分组、优先重跑和输出路径以对应 campaign 记录为准。

### 仅适用于 relay 启动失败的重试

`--retry-relay-startup` 只处理 AiScientist 尚未产生 Agent 工作、提交、评分或调用用量的
relay 启动失败，例如 `AF_UNIX path too long`。使用原 cell 的任务、模型和 variant
参数，在原 `mle-cell` 命令上加该选项。

通过 preflight 后，旧目录移到 `.relay-startup-failures/<任务>/attempt-*/cell/`，
`retry.json` 保存原记录哈希，再创建新尝试。训练失败、已有评分、已消耗调用预算或
暂停中的任务不适用这条路径。relay socket 使用 `/tmp` 下的短目录，训练临时目录按
各 Agent 的 runtime 处理；ML-Master 的短 TMPDIR 例外见其适配文档。

## 新建 Terminal AO run

这是五家比较集合中的一次正式 48h 运行；36 dev 搜索后只运行一次 53-task held-out。
当前 host 调度约定为八卡、dev concurrency 8，各外层 run 按资源串行安排。

```bash
./BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters formal-preflight \
  --benchmark terminal-bench-ao --agent codex --agent-variant codex \
  --protocol terminal-bench-2/ao_protocol/protocol.json \
  --model-config BenchmarkAdapters/configs/model-track.gpt-5.6-terra-host-relay.json

./BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters terminal-ao \
  --agent codex --agent-variant codex \
  --protocol terminal-bench-2/ao_protocol/protocol.json \
  --output-dir /runs/ao/new-campaign/codex/seed-0 --seed 0 \
  --model-config BenchmarkAdapters/configs/model-track.gpt-5.6-terra-host-relay.json \
  --gpu-id 0 --gpu-id 1 --gpu-id 2 --gpu-id 3 \
  --gpu-id 4 --gpu-id 5 --gpu-id 6 --gpu-id 7
```

`--dry-run` 仅检查命令构造，不能作为分数。短预算 smoke 应使用单独的非正式协议/输出
和适用的 smoke 入口，先核对其预算参数；不能把上面的 12h/48h 命令改个目录名就称为
短跑，也不能将 `terminal-direct-smoke` 的直接解题分数混入 AO。旧修复计划的长预算
“smoke”示例已移除。

## 聚合与检查结果

```bash
./BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters mle-scorecard \
  --protocol BenchmarkAdapters/configs/mle-protocol.n1-12h.json \
  --campaign-dir /runs/mle/new-campaign --output /runs/mle-scorecard.json

./BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters terminal-ao-scorecard \
  --protocol terminal-bench-2/ao_protocol/protocol.json \
  --campaign-dir /runs/ao/new-campaign --output /runs/ao-scorecard.json
```

使用实际批次目录；聚合完成不代表所有格子完成。失败/缺失保留分母，MLE 固定 22 题，
AO 固定 53 个 held-out task。检查 `score_valid`、比较集合、模型/硬件/adapter 身份，
同时保留每格官方 grader 输出、预算和 token 记录。

## 已解决问题与仍需遵守的约定

- 正式 MLE 使用 `mle-cell`；旧 `mle` 命令的模型参数接口不能替代冻结 model-track。
- MLE prepared 数据的常规启动检查采用结构/上游 checksum 文件检查；显式重新冻结
  才做完整身份核验。需要逐文件复验时使用上游 `mlebench prepare`，不要把重复读取
  全部数据放到每个 cell 的启动路径。
- native wrapper 在预算结束时保留 Agent 已产出的 submission；ML-Master 还持续同步
  上游最新 best。是否为有效分数仍由 host grader 判断，不能把“文件存在”等同于成功。
- 镜像由 `registry.AGENT_RUNTIME_IMAGES` 指定；AiScientist 使用本地 `aisci-mle:test`，
  Arbor 使用 `alexgshaw/fix-git:20251031`。不要回退到缺少运行依赖的默认镜像。
- N=1 的标准差、SEM、CI 为空。后续 N=3 需另行冻结实验协议，不能在当前批次里临时换 seed。
