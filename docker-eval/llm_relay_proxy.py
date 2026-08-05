#!/usr/bin/env python
"""LLM 转发代理 —— 让所有 agent 以零侵入方式使用 relay/gpt-5.5。

Agent 侧只需把 OPENAI_BASE_URL 指到本代理 (http://127.0.0.1:<port>/v1)，
上游适配逻辑全部集中在这里，agent 代码保持纯上游原版：

  - 模型重写:      请求里的任何 model 统一改写为 LLM_FORCE_MODEL (默认 gpt-5.5)
  - 思考强度:      注入 reasoning_effort=LLM_REASONING_EFFORT (默认 high)
  - 参数清洗:      剥掉 max_tokens / max_completion_tokens / web_search_options 等
                   relay 不支持或不应限制的参数 (LLM_STRIP_PARAMS 可配)
  - 超时:          上游请求默认不设超时 (LLM_UPSTREAM_TIMEOUT 可配秒数)
  - 重试:          连接错误 / 429 / 5xx / 空响应 自动重试 LLM_MAX_RETRIES 次 (默认 20)
  - 非流式化:      agent 请求 stream=True 时，上游走非流式，拿到完整结果后
                   以 SSE 格式一次性回给 agent (规避 relay 掉 chunk 问题)
  - tool 调用兜底: 请求带 tools 但上游没返回 tool_calls 时，自动改写为
                   JSON 文本模式重试，把解析出的 JSON 伪装成 tool_call 响应
  - token 记录:    每次上游调用的 usage 追加写入 LLM_TOKEN_LOG_PATH (jsonl)

用法:
  python llm_relay_proxy.py --port 6200 [--host 127.0.0.1]

环境变量:
  UPSTREAM_BASE_URL     上游 API 地址 (默认 https://relay.shuai-ederson-clow.xyz/v1)
  UPSTREAM_API_KEY      上游 API key (必填, 或复用 OPENAI_API_KEY)
  LLM_FORCE_MODEL       统一改写的模型名 (默认 gpt-5.5; 设为空串禁用重写)
  LLM_REASONING_EFFORT  注入的思考强度 (默认 high; 设为空串禁用注入)
  LLM_STRIP_PARAMS      逗号分隔的待剥参数 (默认 max_tokens,max_completion_tokens,web_search_options)
  LLM_UPSTREAM_TIMEOUT  上游超时秒数 (默认空 = 不限)
  LLM_UPSTREAM_PROXY    上游 HTTP(S) 代理 (默认空 = 直连)
  LLM_MAX_RETRIES       上游重试次数 (默认 20)
  LLM_TOKEN_LOG_PATH    token 用量 jsonl 路径 (默认 llm_token_usage.jsonl)
  LLM_PROXY_AGENT_NAME  写入 token 记录的 agent 名 (默认 unknown)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [proxy] %(levelname)s %(message)s",
)
logger = logging.getLogger("llm_relay_proxy")

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

UPSTREAM_BASE_URL = (
    os.environ.get("UPSTREAM_BASE_URL")
    or "https://relay.shuai-ederson-clow.xyz/v1"
).rstrip("/")
UPSTREAM_API_KEY = os.environ.get("UPSTREAM_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
FORCE_MODEL = os.environ.get("LLM_FORCE_MODEL", "gpt-5.5")
REASONING_EFFORT = os.environ.get("LLM_REASONING_EFFORT", "high")
STRIP_PARAMS = [
    p.strip()
    for p in os.environ.get(
        "LLM_STRIP_PARAMS", "max_tokens,max_completion_tokens,web_search_options"
    ).split(",")
    if p.strip()
]
_timeout_raw = os.environ.get("LLM_UPSTREAM_TIMEOUT", "").strip()
UPSTREAM_TIMEOUT: float | None = float(_timeout_raw) if _timeout_raw else None
UPSTREAM_PROXY = os.environ.get("LLM_UPSTREAM_PROXY", "").strip() or None
MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "20"))
AGENT_NAME = os.environ.get("LLM_PROXY_AGENT_NAME", "unknown")

_token_log_lock = threading.Lock()

# 每线程一个 httpx client（trust_env=False: relay 直连，不走容器代理变量）
_thread_local = threading.local()


def _client() -> httpx.Client:
    client = getattr(_thread_local, "client", None)
    if client is None:
        client = httpx.Client(
            timeout=UPSTREAM_TIMEOUT,
            proxy=UPSTREAM_PROXY,
            trust_env=False,
        )
        _thread_local.client = client
    return client


# ---------------------------------------------------------------------------
# token 记录
# ---------------------------------------------------------------------------

def _token_log_path() -> Path:
    return Path(os.environ.get("LLM_TOKEN_LOG_PATH") or "llm_token_usage.jsonl")


def _usage_get(usage: dict, *keys, default=0):
    for key in keys:
        value = usage.get(key)
        if value:
            return value
    return default


def _append_token_log(model: str, call_type: str, usage: dict | None, duration: float, retries: int) -> None:
    usage = usage or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    record = {
        "timestamp": time.time(),
        "request_id": uuid.uuid4().hex,
        "agent": AGENT_NAME,
        "provider": "relay-proxy",
        "model": model,
        "call_type": call_type,
        "duration_seconds": duration,
        "retries": retries,
        "reasoning_effort": REASONING_EFFORT or None,
        "input_tokens": _usage_get(usage, "prompt_tokens", "input_tokens"),
        "output_tokens": _usage_get(usage, "completion_tokens", "output_tokens"),
        "cache_read_tokens": _usage_get(usage, "cache_read_input_tokens"),
        "cache_write_tokens": _usage_get(usage, "cache_creation_input_tokens"),
        "cached_tokens": prompt_details.get("cached_tokens") or 0,
        "reasoning_tokens": completion_details.get("reasoning_tokens") or 0,
    }
    record["cache_tokens"] = (
        record["cache_read_tokens"] + record["cache_write_tokens"] + record["cached_tokens"]
    )
    record["total_tokens"] = record["input_tokens"] + record["output_tokens"]
    try:
        path = _token_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _token_log_lock, path.open("a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        logger.info(
            "tokens: in=%s out=%s cache=%s total=%s model=%s type=%s dur=%.1fs",
            record["input_tokens"], record["output_tokens"], record["cache_tokens"],
            record["total_tokens"], model, call_type, duration,
        )
    except Exception as exc:
        logger.warning("token log write failed: %s", exc)


# ---------------------------------------------------------------------------
# 请求改写
# ---------------------------------------------------------------------------

def _rewrite_body(body: dict, path: str) -> dict:
    """模型重写 + 思考强度注入 + 参数清洗 + 消息归一化。"""
    if FORCE_MODEL and "model" in body:
        body["model"] = FORCE_MODEL

    for param in STRIP_PARAMS:
        body.pop(param, None)

    # 消息归一化：relay 把 chat 转 Responses API 时，system-only 请求会因
    # input 为空报 400 ("One of input/previous_response_id/... must be provided")。
    # 没有 user 消息时，把最后一条 system 降为 user。
    messages = body.get("messages")
    if isinstance(messages, list) and messages and not any(
        m.get("role") == "user" for m in messages if isinstance(m, dict)
    ):
        for m in reversed(messages):
            if isinstance(m, dict) and m.get("role") == "system":
                m["role"] = "user"
                break

    if REASONING_EFFORT:
        # 强制覆盖：agent 内部无论传什么 effort，统一用代理配置的档位
        if path.endswith("/responses"):
            # Responses API: reasoning={"effort": ...}
            reasoning = body.get("reasoning")
            if not isinstance(reasoning, dict):
                reasoning = {}
            reasoning["effort"] = REASONING_EFFORT
            body["reasoning"] = reasoning
        else:
            body["reasoning_effort"] = REASONING_EFFORT

    return body


def _is_retryable_status(status: int) -> bool:
    return status == 429 or status >= 500


def _post_upstream(path: str, body: dict, call_type: str) -> tuple[dict, float, int]:
    """POST 到上游，带重试。返回 (response_json, duration, retries_used)。"""
    url = f"{UPSTREAM_BASE_URL}{path}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {UPSTREAM_API_KEY}",
    }
    last_error: Exception | None = None
    t0 = time.time()
    for attempt in range(MAX_RETRIES):
        try:
            resp = _client().post(url, json=body, headers=headers)
            if _is_retryable_status(resp.status_code):
                raise RuntimeError(f"upstream {resp.status_code}: {resp.text[:300]}")
            if resp.status_code != 200:
                # 不可重试的 4xx：原样抛给调用方处理
                raise _UpstreamHTTPError(resp.status_code, resp.text)
            data = resp.json()
            _validate_response(data, body)
            return data, time.time() - t0, attempt
        except _UpstreamHTTPError:
            raise
        except Exception as exc:
            last_error = exc
            wait = min(60, 3 * (attempt + 1))
            logger.warning(
                "%s attempt %s/%s failed: %s; retry in %ss",
                call_type, attempt + 1, MAX_RETRIES, exc, wait,
            )
            if attempt < MAX_RETRIES - 1:
                time.sleep(wait)
    raise RuntimeError(f"upstream failed after {MAX_RETRIES} attempts: {last_error}")


class _UpstreamHTTPError(Exception):
    def __init__(self, status: int, text: str):
        super().__init__(f"upstream {status}: {text[:300]}")
        self.status = status
        self.text = text


def _validate_response(data: dict, request_body: dict) -> None:
    """把明显坏掉的成功响应当作失败触发重试（空 choices / 空内容）。"""
    choices = data.get("choices")
    if choices is None:
        return  # 非 chat 响应（如 /responses、/models），不校验
    if not choices:
        raise RuntimeError("upstream returned empty choices")
    message = choices[0].get("message") or {}
    if not (message.get("content") or "").strip() and not message.get("tool_calls"):
        raise RuntimeError("upstream returned empty message (no content, no tool_calls)")


# ---------------------------------------------------------------------------
# tool 调用兜底
# ---------------------------------------------------------------------------

def _parse_json_object(text: str) -> dict:
    """从模型文本里尽力解析出一个 JSON 对象。"""
    text = (text or "").strip()
    candidates = [text]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1))
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception as exc:
            last_error = exc
    raise ValueError(f"cannot parse JSON from model text: {text[:300]!r}") from last_error


def _tool_call_fallback(body: dict) -> tuple[dict, float, int]:
    """tools 请求没拿到 tool_calls 时：改写成 JSON 文本模式重试，再合成 tool_call 响应。"""
    tools = body.get("tools") or []
    func = (tools[0].get("function") or {}) if tools else {}
    name = func.get("name", "structured_output")
    schema = json.dumps(func.get("parameters") or {}, ensure_ascii=False)

    fallback = {k: v for k, v in body.items() if k not in ("tools", "tool_choice", "stream", "stream_options")}
    messages = list(fallback.get("messages") or [])
    instruction = (
        f"\n\nReturn ONLY one valid JSON object for `{name}`. "
        "No markdown, no explanations, no code fences. "
        f"It must satisfy this JSON schema:\n{schema}"
    )
    if messages and messages[-1].get("role") == "user":
        messages[-1] = {**messages[-1], "content": str(messages[-1].get("content") or "") + instruction}
    else:
        messages.append({"role": "user", "content": instruction.strip()})
    fallback["messages"] = messages

    data, duration, retries = _post_upstream("/chat/completions", fallback, "chat.tool_json_fallback")
    content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    parsed = _parse_json_object(content)

    # 合成 OpenAI tool_call 格式响应
    synthesized = dict(data)
    synthesized["choices"] = [{
        "index": 0,
        "message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(parsed, ensure_ascii=False)},
            }],
        },
        "finish_reason": "tool_calls",
    }]
    return synthesized, duration, retries


# ---------------------------------------------------------------------------
# SSE 合成（agent 请求 stream=True 时）
# ---------------------------------------------------------------------------

def _synthesize_sse(data: dict) -> bytes:
    """把完整 chat completion 合成为 SSE 字节流。"""
    created = data.get("created", int(time.time()))
    model = data.get("model", "")
    chunk_id = data.get("id", f"chatcmpl-{uuid.uuid4().hex[:24]}")
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}

    def chunk(delta: dict, finish_reason=None, usage=None) -> str:
        payload = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        if usage is not None:
            payload["usage"] = usage
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    parts = [chunk({"role": "assistant"})]
    content = message.get("content")
    if content:
        parts.append(chunk({"content": content}))
    tool_calls = message.get("tool_calls")
    if tool_calls:
        deltas = []
        for i, tc in enumerate(tool_calls):
            deltas.append({
                "index": i,
                "id": tc.get("id"),
                "type": "function",
                "function": tc.get("function", {}),
            })
        parts.append(chunk({"tool_calls": deltas}))
    parts.append(chunk({}, finish_reason=choice.get("finish_reason", "stop"), usage=data.get("usage")))
    parts.append("data: [DONE]\n\n")
    return "".join(parts).encode("utf-8")


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静音默认 access log（token 记录已含关键信息）
        pass

    def _send_json(self, status: int, payload: dict | bytes, content_type="application/json") -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            model = FORCE_MODEL or "gpt-5.5"
            self._send_json(200, {
                "object": "list",
                "data": [{"id": model, "object": "model", "created": 0, "owned_by": "relay-proxy"}],
            })
            return
        if self.path in ("/", "/health", "/healthz"):
            self._send_json(200, {"status": "ok", "upstream": UPSTREAM_BASE_URL, "model": FORCE_MODEL})
            return
        self._send_json(404, {"error": {"message": f"not found: {self.path}"}})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw or b"{}")
        except Exception as exc:
            self._send_json(400, {"error": {"message": f"invalid JSON body: {exc}"}})
            return

        # 归一化路径: /v1/chat/completions 或 /chat/completions 都接受
        path = self.path.split("?")[0]
        if path.startswith("/v1/"):
            path = path[3:]

        try:
            if path == "/chat/completions":
                self._handle_chat(body)
            else:
                self._handle_generic(path, body)
        except _UpstreamHTTPError as exc:
            logger.error("upstream 4xx passthrough: %s", exc)
            try:
                payload = json.loads(exc.text)
            except Exception:
                payload = {"error": {"message": exc.text[:1000]}}
            self._send_json(exc.status, payload)
        except Exception as exc:
            logger.error("proxy error on %s: %s", path, exc)
            self._send_json(502, {"error": {"message": f"relay proxy failure: {exc}"}})

    # -- chat completions 主路径 --

    def _handle_chat(self, body: dict) -> None:
        client_wants_stream = bool(body.pop("stream", False))
        body.pop("stream_options", None)
        body = _rewrite_body(body, "/chat/completions")
        has_tools = bool(body.get("tools"))
        model = body.get("model", "")

        data, duration, retries = _post_upstream("/chat/completions", body, "chat.completions")

        call_type = "chat.completions"
        message = ((data.get("choices") or [{}])[0].get("message")) or {}
        if has_tools and not message.get("tool_calls"):
            logger.warning("tools requested but no tool_calls returned; using JSON fallback")
            # 第一次调用的 token 也真实消耗了，单独记账再走兜底
            _append_token_log(model, "chat.completions.discarded", data.get("usage"), duration, retries)
            data, duration, retries = _tool_call_fallback(body)
            call_type = "chat.tool_json_fallback"

        _append_token_log(model, call_type + (".stream" if client_wants_stream else ""),
                          data.get("usage"), duration, retries)

        if client_wants_stream:
            self._send_json(200, _synthesize_sse(data), content_type="text/event-stream")
        else:
            self._send_json(200, data)

    # -- 其余路径（/responses, /embeddings, /completions...）通用转发 --

    def _handle_generic(self, path: str, body: dict) -> None:
        body = _rewrite_body(body, path)
        model = body.get("model", "")
        data, duration, retries = _post_upstream(path, body, path.strip("/"))
        _append_token_log(model, path.strip("/"), data.get("usage"), duration, retries)
        self._send_json(200, data)


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM relay proxy")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6200)
    args = parser.parse_args()

    if not UPSTREAM_API_KEY:
        logger.warning("UPSTREAM_API_KEY / OPENAI_API_KEY 未设置，上游调用将失败")

    server = ThreadingHTTPServer((args.host, args.port), ProxyHandler)
    logger.info(
        "listening on %s:%s → %s | model=%s effort=%s strip=%s timeout=%s retries=%s agent=%s",
        args.host, args.port, UPSTREAM_BASE_URL, FORCE_MODEL or "(passthrough)",
        REASONING_EFFORT or "(none)", ",".join(STRIP_PARAMS), UPSTREAM_TIMEOUT, MAX_RETRIES, AGENT_NAME,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
