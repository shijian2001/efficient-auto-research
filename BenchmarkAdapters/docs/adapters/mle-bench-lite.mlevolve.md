# MLEvolve × MLE-Bench Lite

| | |
|---|---|
| 形态 | 原生 Docker 启动器 |
| registry `mle_backend` | `native-docker` |
| 源码树 | `baselines/MLEvolve` |
| variant | 原版 ID（无 variant） |
| 入口 | `MLEBenchLite/adapter.py::_docker_command` → `docker-eval/run_in_docker.sh MLEvolve` |

## 做法

对齐上游官方 `run_single_task.sh`：除必要的路径 / LLM 端点 / 时长外，
一律用上游默认值。`global_memory`、`parallel_search`、`debug_depth`、
`initial_drafts` **都不覆盖**。搜索与融合完全由 MLEvolve 引擎自己决定。

CPU 配额 21 核，与官方 `CPUS_PER_TASK=21` 对齐。

### 唯一的能力性偏离：关掉 cold-start

`coldstart.use_coldstart=False`，显式关闭。

原因：cold-start 依赖的 `competition_tag_classified.json` 硬编码了 MLE-bench
全部 75 题的「竞赛 → 任务类别」映射，属于针对本 benchmark 的预计算适配——
运行时本应由 Agent 自己读题判断。在对照实验里保留它，对没有知识库的 EAR 不公平。

当前研究协议只保留**无 cold-start 的纯原版 baseline**，不提供运行时开关。
（EAR 侧对应的 `g3_legacy_mle_knowledge` 模式同样不启用。）

### exp_name 三段式

MLEvolve 的 `result_parse_agent` 用 `split('_')[2]` 取 exp_id 做格式校验，
而 `config.__init__` 会自动加 `YYYYmmdd_HHMMSS_` 前缀。所以传入 `$COMP`
即可满足三段式，不需要 adapter 额外拼接。

### 源码隔离

要求 `MLE_AGENT_DIR` 的 tracked 源码 clean，`archive_tracked_source` 出只含
tracked 文件的快照后挂载。每格一个 `MLE_SESSION_ROOT`
（`$MLE_RUN_ROOT/session_${RUN_TAG}_${COMP}`），已存在则直接退出 2，
避免不同 run 的 journal / workspace / fusion 产物互相污染。

### host 侧 format server

MLEvolve 需要一个 submission 格式校验服务。Adapter 在 **host** 上起
`engine.validation.format_server`（`GRADING_SERVER_PORT=$GRADING_PORT`），
容器通过端口访问。这样容器拿不到完整 benchmark data root，也拿不到 private label——
校验只回答"格式对不对"，不回答"分数多少"。

健康检查最多等 30 秒（60 × 0.5s）。

## 产物

`artifact_path = output_dir/submission.csv`。跑完后照官方脚本做 submission
fusion（top solutions 集成），最终产物仍由 MLEvolve 自己声明。
新鲜度 / 非空 / 非 sample 判定与其他格一致。

## Relay

同 EAR：`native-docker` 路径的 relay 由 `run_in_docker.sh` 在 host 起，
不是 `MleLiteAdapter.run` 里的 `RelayProcess`。
