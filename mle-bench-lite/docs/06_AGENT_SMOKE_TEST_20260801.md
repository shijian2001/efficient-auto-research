# MLEvolve 与 Efficient Agent Research 冒烟测试

测试时间：2026-08-01（Asia/Shanghai）

## 测试条件

- Benchmark：MLE-Bench Lite 已准备数据
- Competition：`spooky-author-identification`
- GPU：每个 Agent 使用 1×RTX 4090
- 模型：`gpt-5.5`
- API：OpenAI-compatible relay，Base URL 为 `https://relay.shuai-ederson-clow.xyz/v1`
- 搜索步数：1
- 外层时限：600 秒
- 密钥仅通过环境变量传入，没有写入仓库或运行清单

## API 验证

- `/v1/models` 请求成功，模型列表包含 `gpt-5.5`
- Chat Completions 请求成功，模型返回预期内容

## MLEvolve

- Agent 正常读取题目、判断优化方向、加载全局记忆并完成三阶段代码生成。
- 配置：纯原版 `main@fe92521`，`coldstart=False`。
- 共完成 7 次模型请求，合计 76,018 tokens。
- 高推理模型生成和代码审查耗时较长，候选代码在 600 秒外层时限结束前约 1 秒才开始执行，因此原容器被冒烟时限终止。
- 不再调用模型，单独执行已经生成并审核通过的候选脚本，20 秒内完成训练并生成 submission。
- 本地验证 log loss：`0.34358115845442794`。
- 官方 `mlebench grade-sample` 评分：`0.3386`。
- 官方 grader 结果：submission 有效、优于中位数阈值 `0.418785`、未达到铜牌阈值 `0.29381`。

关键产物：

- Agent 日志：`baselines/MLEvolve/runs/20260801_073513_spooky-author-identification/logs/MLEvolve.log`
- 生成代码：`baselines/MLEvolve/runs/20260801_073513_spooky-author-identification/workspace/runfile_0.py`
- Submission：`baselines/MLEvolve/runs/20260801_073513_spooky-author-identification/workspace/submission/submission_ec94c893121b4ad88d802ff650fa71a9.csv`
- 官方评分：`run-logs/smoke_gpt55_20260801_153449/mlevolve_official_grade.txt`
- Token 日志：`run-logs/smoke_gpt55_20260801_153449_token_usage/MLEvolve_spooky-author-identification_gpu4.jsonl`

结论：MLEvolve 可以在本机 RTX 4090、该 relay 和 `gpt-5.5` 上运行 MLE-Bench Lite。正式实验需要使用远长于 10 分钟的预算。

## Efficient Agent Research

- 默认 `EAR_CLI_MODE=current` 的 Docker launcher 与当前 Agent 分支 CLI 不一致，会传入该分支不支持的新参数。
- 改用与当前分支兼容的 `EAR_CLI_MODE=g3_legacy` 后，同一份 Agent 源码正常启动。
- Agent 完成 2 次模型请求，合计 15,799 tokens，并成功生成和执行候选代码。
- 首个候选使用了当前 scikit-learn 已不接受的 `LogisticRegression(multi_class=...)` 参数，执行以 `TypeError` 结束，因此单步冒烟没有生成 submission。
- 该失败发生在候选方案代码中，不是 API、模型、GPU、数据读取或 Agent 启动失败。多步搜索可以把错误反馈给下一步，但本次为控制成本没有启动新的完整多步实验。

关键产物：

- 完整日志：`run-logs/smoke_gpt55_earlegacy_20260801_153925/ear-full.log`
- 生成代码：`mle-bench-agents/efficient-auto-research/docker_runs/smoke_gpt55_earlegacy_20260801_153925_spooky-author-identification/workspace/step_000.py`
- 执行 trace：`mle-bench-agents/efficient-auto-research/docker_runs/smoke_gpt55_earlegacy_20260801_153925_spooky-author-identification/workspace/traces/step_000.json`
- 运行报告：`mle-bench-agents/efficient-auto-research/docker_runs/smoke_gpt55_earlegacy_20260801_153925_spooky-author-identification/workspace/report.json`
- Token 日志：`run-logs/smoke_gpt55_earlegacy_20260801_153925_token_usage/efficient-auto-research_spooky-author-identification_gpu5.jsonl`

结论：Efficient Agent Research 可以在相同环境中启动、调用 `gpt-5.5`、生成并执行 MLE-Bench Lite 候选；当前 launcher 应使用兼容 CLI 模式，单步测试尚未获得有效 submission。
