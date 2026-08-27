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
    LLM_UPSTREAM_API=responses \
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

### 为什么必须 `LLM_UPSTREAM_API=responses`

默认 `chat` 会把 Responses 请求降级成 chat completions，而 **Codex 的工具声明
在这一步会被整个丢掉**。Codex 不用标准的 `tools` 字段，它把工具放在 input 数组里
一个 `{"type":"additional_tools", ...}` 条目中；转换器只认 `body["tools"]`，
那个条目既不是 role message 又没有 content，于是被跳过。

结果是模型收到「有任务、但没有任何工具」，Codex 正常启动、正常烧 token，然后回

    I'm unable to access the filesystem in this session

**不报错、不超时，看起来像模型能力不行。** 这是最贵的一种失败：一格烧满预算却交白卷。

`responses` 直连把请求原样透传给上游，工具声明完整保留。model track 的重写不受影响：
temperature 与 reasoning effort 仍然强制覆盖成 track 里的值。

`max_tokens` / `max_output_tokens` 是例外：campaign **不注入**（model track 里没有这一项），
但 Agent 自己设的会原样保留——那是 Agent 自己的预算，不是我们该替它决定的采样参数。

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
- **prepared 数据的完整性由上游负责，我们不再重复校验。**
  原先 `validate_lite_data_root` 会按内容重新哈希全部 22 题的 prepared
  public+private 树：一轮读约 135 GB，本机这组盘满负载只有约 16 MB/s，单次要按
  小时算，而 154 格每格都要重付一次。

  这层是我们自己加的（`cc6b84f`），上游 mle-bench 本来就在
  `mlebench prepare` 时为每个 competition 生成并核对 public/private 的逐文件
  checksum，存在 `competitions/<task>/checksums.yaml`（22 题全都有）。等于同一件事
  做了两遍，而且我们这遍做了 154 次。

  现在只做结构检查：prepared public/private 存在、非符号链接、非空，且上游
  checksums.yaml 在位。实测 **0.70 秒**（原先一小时以上）。

  仍然按内容校验的部分没变：每格自己的源码归档 `verify_task_archive`
  （只哈希本格那一个），以及 `data_manifest.json` 记录的身份——成绩单绑定的
  东西一个没少。要重新逐文件校验 prepared 数据，跑上游
  `mlebench prepare`（不加 `--skip-verification`），或显式的
  `mle-freeze-assets`。

- **用满时间不再等于零分（MLE-011 已修）。** 协议里的 `wall_clock_seconds`
  （正式 MLE 12h、AO 48h）是每格给 Agent 的解题时间。原先唯一的强制手段是
  `预算+120s` 时 SIGKILL 整个进程树，那一刀连我们自己的 wrapper 一起砍，
  于是 Agent 早已写好的 `submission.csv` 来不及被拷进成绩目录。ML-Master 因此
  把一个官方 grader 打 0.91987、够金牌的结果记成了 `timed_out / score: null`。

  现在 native wrapper 自己在预算点停 Agent（先 SIGTERM，20s 不退再 SIGKILL），
  剩下的窗口用来发布产物；外层硬 kill 退化成保底。**Agent 的解题时间没有变**，
  变的只是"超时"的含义：从"你做的全作废"变成"时间到，交已经做出来的"。
  子进程真失败仍然照常报错，不会被当成功发布。

- **N=1 没有误差棒。** `repetition_summary` 只给 `mean`，
  standard_deviation / standard_error / ci95 全为 `null`。排名不得表述为显著差异。
