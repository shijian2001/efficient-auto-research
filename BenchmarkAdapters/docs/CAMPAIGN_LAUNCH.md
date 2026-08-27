# 正式 campaign 启动手册

本轮（2026-08-27）冻结配置：**gpt-5.6-terra**，**N=1**，MLE 12h / AO 48h。

## 0. 先起 host relay —— 最容易漏的一步

model-track 里的 `relay_base_url` 是 `http://127.0.0.1:6200/v1`。这个端口
**不会被 adapter 自动拉起**：每格自己起的那个 relay 是 Unix-socket 的
per-run 转发器，它的上游正是 6200。6200 没人监听时，症状是

```
[proxy] WARNING chat.completions attempt 1/21 failed: [Errno 111] Connection refused
```

然后按 `max_retries` 一路重试到超时。**不会**报 "relay 没起"，所以很容易
误判成模型或网络问题。

启动（跑完整个 campaign 期间保持存活）：

```bash
cd /mnt/sdc/shijianwang/efficient-agent-research
env UPSTREAM_BASE_URL="$OPENAI_BASE_URL" \
    UPSTREAM_API_KEY="$OPENAI_API_KEY" \
    LLM_FORCE_MODEL="gpt-5.6-terra" \
    LLM_FORCE_PARAMETERS_JSON='{"temperature":1.0,"reasoning_effort":"high"}' \
    LLM_UPSTREAM_TIMEOUT=600 \
    LLM_MAX_RETRIES=20 \
    LLM_PROXY_API_KEY="$OPENAI_API_KEY" \
    LLM_TOKEN_LOG_PATH=/path/to/host_relay_tokens.jsonl \
    LLM_PROXY_AGENT_NAME=host-relay \
    ./BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters.LLMRelay.server \
    --port 6200 --host 127.0.0.1
```

`LLM_PROXY_API_KEY` 是**入站**凭据，必须设成 per-run relay 会发过来的那把 key。
per-run relay 取 `UPSTREAM_API_KEY` 或 `OPENAI_API_KEY` 作为 Authorization 发给
6200；而 host relay 默认只认字面量 `"proxy"`（`server.py:97`）。两边不一致时
每格会在 1 秒内失败（`preflight` 的 `host_relay_reachable` 现在会先拦住）：

```
[proxy] ERROR upstream 4xx passthrough: upstream 401: {"error": {"message": "invalid relay credential"}}
result.json: status=failed  failure_reason=RuntimeError: relay upstream readiness returned 401
```

这一条和上面的 6200 没起是**两个独立的坑**，会依次踩到。正式 preflight 里的
`host_relay_reachable` 会同时挡住这两种情况：它用 per-run relay 实际会发的那把
凭据向 `relay_base_url` 真发一次请求，并核对返回的 model 与 model-track 一致，
所以起漏了、key 配错了、`LLM_FORCE_MODEL` 写错了，都会在开跑前就报出来。

`LLM_FORCE_MODEL` 与 `LLM_FORCE_PARAMETERS_JSON` 必须与 model-track 一致，
否则成绩单记的模型身份和实际调用不符。relay 会把任何 model 名改写成
`LLM_FORCE_MODEL`，所以 Agent 侧传什么名字都不影响实际模型。

自检：

```bash
curl -s http://127.0.0.1:6200/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -d '{"model":"anything","messages":[{"role":"user","content":"Reply with exactly: RELAY_OK"}]}'
```

返回里 `model` 应为 `gpt-5.6-terra`。

## 1. 冻结的协议与 model-track

| 用途 | 路径 |
|---|---|
| MLE 协议 | `BenchmarkAdapters/configs/mle-protocol.n1-12h.json`（22 题 / seed 0 / 12h） |
| AO 协议 | `terminal-bench-2/ao_protocol/protocol.json`（36/53 / seed 0 / 48h） |
| model-track | `BenchmarkAdapters/configs/model-track.gpt-5.6-terra-host-relay.json` |

## 2. 每格必须传的 `--agent-variant`

`agent_variant_explicit` 门禁**拒绝 `default`**。各格合法值：

| Agent | MLE | Terminal AO |
|---|---|---|
| EAR | `ear` | `ear` |
| MLEvolve | `mlevolve` | 不参与 |
| Arbor | `arbor-benchmark-patched` | `arbor@92c6fd5c22c8a291796d39730605ac0eb8ba07c5` |
| Codex | `codex` | `codex` |
| Claude Code | `claude-code` | `claude-code` |
| ML-Master 2.0 | `ml-master-2@07a80dac7f9edad18f2d97bcbffc0585e06d5b46` | 不参与 |
| AiScientist | `ai-scientist@770039abc8f1319f436542b16f630d70d117d322` | `ai-scientist-terminal-variant` |

带 `@` 的那三家用的是**本机 pin**，不是上游 tip。写错会被
`require_clean_upstream_source` 拒（报错会给出正确哈希）。来源见
`ON_DISK_AGENT_VERSIONS.md`。

## 3. 先 preflight 再开跑

```bash
./BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters formal-preflight \
  --benchmark mle-bench-lite --agent <agent> --agent-variant <见上表> \
  --protocol BenchmarkAdapters/configs/mle-protocol.n1-12h.json \
  --model-config BenchmarkAdapters/configs/model-track.gpt-5.6-terra-host-relay.json \
  --data-root mle-bench-data
```

14 项全 PASS 才开跑。注意 `formal_source_clean` 查的是**工作区当下状态**，
有未提交改动就会失败——先提交再 preflight。

## 4. 跑一格

MLE（`mle-cell` 是正式入口，走 `--model-config`）：

```bash
./BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters mle-cell \
  --protocol BenchmarkAdapters/configs/mle-protocol.n1-12h.json \
  --agent <agent> --agent-variant <见上表> \
  --competition-id <task> --seed 0 \
  --data-root mle-bench-data --campaign-dir <campaign> --gpu-id <n> \
  --model-config BenchmarkAdapters/configs/model-track.gpt-5.6-terra-host-relay.json
```

AO 一次独占 8 卡（`dev_concurrency=8`），所以 5 家只能串行：

```bash
./BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters terminal-ao \
  --agent <agent> --agent-variant <见上表> \
  --protocol terminal-bench-2/ao_protocol/protocol.json \
  --output-dir <out> --seed 0 \
  --model-config BenchmarkAdapters/configs/model-track.gpt-5.6-terra-host-relay.json \
  --gpu-id 0 --gpu-id 1 --gpu-id 2 --gpu-id 3 \
  --gpu-id 4 --gpu-id 5 --gpu-id 6 --gpu-id 7
```

## 已知坑

- **`mle` 子命令实跑不了。** 它只接 `--model` + `--upstream-base-url`，不传
  `model_parameters`，relay 会抛
  `RuntimeError: relay model parameters must be configured explicitly`。
  调试用 `--dry-run`；实跑一律用 `mle-cell`。
- **资产校验现在带缓存。** `validate_lite_data_root` 要按内容哈希全部 22 题的
  prepared public+private 树，一轮读 135 GB；本机这组盘在满负载下只有约
  16 MB/s，单次要按小时算，而 154 格原本每格都要重付一次。

  现在按「stat 指纹（每文件 size/mtime/inode）+ manifest digest」缓存校验结论，
  存在 `cache/mle-asset-verification/`（已被 `.gitignore` 覆盖，不会弄脏工作区）。
  指纹变了就重新按内容哈希，所以改动、替换、换 manifest 都会立即失效——
  实测同长度篡改仍会被抓出 `MLE prepared public asset drift`。
  想强制全量重读：`MLE_ASSET_VERIFY=full`。

  各格自己的源码归档校验（`verify_task_archive`）只哈希本格那一个，不是全量，
  不走这个缓存。
- **N=1 没有误差棒。** `repetition_summary` 只给 `mean`，
  standard_deviation / standard_error / ci95 全为 `null`。排名不得表述为显著差异。
