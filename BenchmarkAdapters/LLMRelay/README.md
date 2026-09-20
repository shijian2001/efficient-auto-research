# Repository-owned LLM relay

The relay keeps provider credentials on the host, applies the frozen model track,
records usage, and exposes a run-local loopback or Unix-socket endpoint to each
Agent. Agents normally authenticate to that local endpoint with the placeholder
credential `proxy`.

## Host service and per-run service

There are two distinct layers:

- The shared MLE host relay is started by `docker-eval/start_mle_host_relay.sh`.
  The current shared model-track points to `127.0.0.1:6201/v1`; the script uses the
  host download proxy on port 17892 for its upstream connection.
- `RelayProcess` creates an isolated relay for each run. A sandbox's local port
  6200 or its Unix socket is a downstream entrance, not the shared host service.
  Docker MLE runners use allocated host/bridge ports rather than requiring one
  fixed global `6200 + GPU_ID` mapping.

A controller may supply a different per-run model-track. The paused September 20
v7 campaign has slot-specific copies pointing to 6202/6203 and pinned provider
credentials. Its saved configuration takes precedence over the shared default.
See the [campaign runbook](../docs/CAMPAIGN_LAUNCH.md) for lifecycle and commands,
and [key-pool documentation](../docs/RELAY_KEY_POOL.md) for credential selection.

## Protocol behavior

The behavior below follows `server.py`, including `_post_canonical_chat`,
`_handle_responses`, `_handle_messages`, and `_rewrite_body`.

| Downstream request | Default upstream request |
|---|---|
| OpenAI Chat Completions | Chat Completions |
| OpenAI Responses | Responses |
| Anthropic-shaped Messages | Converted to Chat Completions, then converted back for the client |

Chat and Responses preserve their native protocol fields; Messages is a bridge,
not a provider-specific Messages upstream. `LLM_FORCE_CROSS_PROTOCOL=1` enables
forced conversion to the endpoint selected by `LLM_UPSTREAM_API` (`chat` or
`responses`). This is off by default. Forced conversion can lose protocol-specific
fields, so it is not the normal way to connect mixed Agents.

Sampling/control fields are replaced by the frozen model track. Model identity,
reasoning effort and temperature are controlled by the host; temperature is
forced to 1.0. Protocol fields such as messages/input, tools and tool choice are
retained according to the endpoint's allowlist.

**Output-token caps are not injected by the model track.** Legacy caps in the
track are removed, but an Agent's own `max_tokens`, `max_completion_tokens` or
`max_output_tokens` is preserved for its corresponding protocol. The earlier
claim that the relay strips every client output cap was incorrect.

Truncation signals (`length`, `max_tokens`, `incomplete` and related details) must
remain visible through conversions and synthesized SSE. For `tool_choice=auto`,
a text-only answer is returned unchanged. An explicitly required function call
without `tool_calls` is a protocol error; the relay does not synthesize a tool
call or ask the model for a replacement text JSON payload.

## Usage and diagnostics

Token logs include input/output usage and available cached/reasoning token
fields. Cached input tokens are a subset of input tokens, so they are not added
a second time to `total_tokens`. Missing upstream usage remains unknown rather
than being invented as zero. The logging path is supplied per service/run.

Inbound host credentials must match the key actually sent by the per-run relay.
Connection refusal, inbound 401 and provider failure are different conditions;
`formal-preflight` probes the configured host relay with a real model request.
It is not an offline status command. Startup instructions live only in the
[campaign runbook](../docs/CAMPAIGN_LAUNCH.md).

## Historical cache-accounting repair (2026-08-27)

**cache token 记账（2026-08-27 修复）**：上游 `gpt-5.6-terra` 两个端点报的 usage 形状不同——
`/v1/chat/completions` 把命中数放在 `usage.prompt_tokens_details.cached_tokens`（实测长 prompt
重复请求：`prompt_tokens=11207`，其中 `cached_tokens=11008`），`/v1/responses` 则放在
`usage.input_tokens_details.{cached_tokens,cache_write_tokens}`、reasoning 放在
`output_tokens_details.reasoning_tokens`。原 `_append_token_log` 只读 `prompt_tokens_details` /
`completion_tokens_details`，且要求 Anthropic 与 OpenAI 三个字段**同时**非空才算 `cache_tokens`，
结果所有真实命中都被写成 `cache_tokens=None`（387 条 host 日志里 111 条有 `cached_tokens`、
0 条有 `cache_tokens`），并让 `summarize_token_log` 把整段 cache 汇总降级为 None。现改为按端点
回退取值、只要有任一 cache 信号就求和；上游完全不报时仍为 None（保持「未知」而非伪造 0）。
`total_tokens = input + output` 保持不变且**正确**：OpenAI 约定里 `cached_tokens` 是
`prompt_tokens` 的子集而非增量（实测 11207+5=11212=上游 `total_tokens`），加上去会重复计数。

## Historical transport note

The old `docs/relay-smoke.md` record, committed on 2026-08-05, used model `gpt-5.5`,
endpoint `https://relay.shuai-ederson-clow.xyz/v1` and proxy `127.0.0.1:17892`.
That test reached GitHub through HTTP CONNECT/SOCKS5 and resolved the relay host,
but the relay TLS handshake timed out and `/v1/models` returned no payload.
This is retained as a historical transport diagnosis, not a claim about the
current relay's availability.
