#!/usr/bin/env python
"""Repository-owned LLM relay server for every benchmark Agent request.

Agent 侧只需把 OPENAI_BASE_URL 指到本代理 (http://127.0.0.1:<port>/v1)，
上游适配逻辑全部集中在这里，agent 代码保持纯上游原版：

  - 下游协议:      接受 OpenAI Chat、OpenAI Responses 和 Messages
  - 上游协议:      只发送 OpenAI-compatible Chat 或 Responses
  - 模型重写:      请求里的任何 model 统一改写为必填 LLM_FORCE_MODEL
  - 推理参数:      仅注入 LLM_FORCE_PARAMETERS_JSON 中明确配置的参数
  - 参数清洗:      只保留协议载荷，模型生成参数由冻结 model track 注入
  - 超时:          上游请求默认不设超时 (LLM_UPSTREAM_TIMEOUT 可配秒数)
  - 重试:          连接错误 / 429 / 5xx / 空响应按显式 LLM_MAX_RETRIES 配置重试
  - 非流式化:      agent 请求 stream=True 时，上游走非流式，拿到完整结果后
                   以 SSE 格式一次性回给 agent (规避 relay 掉 chunk 问题)
  - tool 调用:     auto 文本原样返回；required/function 缺少 tool_calls 时
                   返回协议错误，绝不伪造工具调用
  - token 记录:    每次上游调用的 usage 追加写入 LLM_TOKEN_LOG_PATH (jsonl)

用法:
  python -m BenchmarkAdapters.LLMRelay.server --port 6200 [--host 127.0.0.1]

环境变量:
  UPSTREAM_BASE_URL     上游 API 地址（必填）
  UPSTREAM_API_KEY      上游 API key (必填, 或复用 OPENAI_API_KEY)
  LLM_FORCE_MODEL       统一改写的模型名（必填）
  LLM_FORCE_PARAMETERS_JSON  统一模型参数 JSON 对象（必填且不可为空）
  LLM_UPSTREAM_TIMEOUT  上游超时秒数 (默认空 = 不限)
  LLM_UPSTREAM_PROXY    上游 HTTP(S) 代理 (默认空 = 直连)
  LLM_MAX_RETRIES       上游重试次数（必需由 launcher/model track 显式注入）
  LLM_UPSTREAM_API      chat 或 responses（默认 chat）
  LLM_TOKEN_LOG_PATH    token 用量 jsonl 路径 (默认 llm_token_usage.jsonl)
  LLM_PROXY_AGENT_NAME  写入 token 记录的 agent 名 (默认 unknown)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socketserver

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [proxy] %(levelname)s %(message)s",
)
logger = logging.getLogger("llm_relay_proxy")

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

UPSTREAM_BASE_URL = os.environ.get("UPSTREAM_BASE_URL", "").rstrip("/")
UPSTREAM_API_KEY = os.environ.get("UPSTREAM_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
FORCE_MODEL = os.environ.get("LLM_FORCE_MODEL", "").strip()
try:
    _configured_force_parameters = json.loads(
        os.environ.get("LLM_FORCE_PARAMETERS_JSON", "{}")
    )
except json.JSONDecodeError as exc:
    raise RuntimeError("LLM_FORCE_PARAMETERS_JSON is invalid") from exc
if not isinstance(_configured_force_parameters, dict):
    raise RuntimeError("LLM_FORCE_PARAMETERS_JSON must be an object")
# Output caps are intentionally not part of the benchmark model track. Remove
# them defensively even if an older launcher or config still supplies one.
_OUTPUT_CAP_FIELDS = frozenset({"max_output_tokens", "max_completion_tokens", "max_tokens"})
FORCE_PARAMETERS = {
    name: value
    for name, value in _configured_force_parameters.items()
    if name not in _OUTPUT_CAP_FIELDS
}
REASONING_EFFORT = str(FORCE_PARAMETERS.get("reasoning_effort", ""))
# Sampling is a shared benchmark control: every upstream request uses exactly
# temperature=1.0, regardless of client or legacy model-track settings.
FORCE_PARAMETERS["temperature"] = 1.0
TEMPERATURE = "1.0"
_timeout_raw = os.environ.get("LLM_UPSTREAM_TIMEOUT", "").strip()
UPSTREAM_TIMEOUT: float | None = float(_timeout_raw) if _timeout_raw else None
UPSTREAM_PROXY = os.environ.get("LLM_UPSTREAM_PROXY", "").strip() or None
_max_retries_raw = os.environ.get("LLM_MAX_RETRIES")
MAX_RETRIES = int(_max_retries_raw) + 1 if _max_retries_raw is not None else 0
_max_upstream_calls_raw = os.environ.get("LLM_MAX_UPSTREAM_CALLS", "").strip()
MAX_UPSTREAM_CALLS: int | None = (
    int(_max_upstream_calls_raw) if _max_upstream_calls_raw else None
)
if MAX_UPSTREAM_CALLS is not None and MAX_UPSTREAM_CALLS < 1:
    raise RuntimeError("LLM_MAX_UPSTREAM_CALLS must be positive")
AGENT_NAME = os.environ.get("LLM_PROXY_AGENT_NAME", "unknown")
INBOUND_API_KEY = os.environ.get("LLM_PROXY_API_KEY", "proxy")
# Escape hatch, off by default: force every client onto the single upstream
# protocol named by LLM_UPSTREAM_API, translating whatever does not match. That
# is what the relay always used to do, and it is why tools went missing. Keep it
# only for an upstream that genuinely serves one endpoint.
_FORCE_CROSS_PROTOCOL = os.environ.get(
    "LLM_FORCE_CROSS_PROTOCOL", ""
).strip().lower() in {"1", "true", "yes"}
_configured_upstream_api = os.environ.get("LLM_UPSTREAM_API", "").strip().lower()
UPSTREAM_API = _configured_upstream_api or (
    "responses"
    if str(FORCE_PARAMETERS.get("api_mode", "")).strip().lower() == "responses"
    and FORCE_PARAMETERS.get("use_completion_api") is not True
    else "chat"
)
if UPSTREAM_API not in {"chat", "responses"}:
    raise RuntimeError("LLM_UPSTREAM_API must be chat or responses")

_CONTROL_PARAMETERS = {
    "api_mode",
    "context_window",
    "max_completion_tokens",
    "max_output_tokens",
    "max_tokens",
    "use_completion_api",
}

# Only protocol fields supplied by a client are preserved. Every other
# top-level request field is a model-generation/control parameter and must be
# supplied by the frozen model track, not by one particular Agent.
# Fields forwarded upstream as the client sent them. The output-token limit is
# included deliberately: the campaign never injects one (the model track carries
# no max_tokens), but an Agent that sets its own budget must keep it rather than
# have it silently stripped. Everything outside this set is dropped and reported
# through _request_telemetry so the frozen track stays the only source of
# sampling parameters.
_PROTOCOL_FIELDS = {
    "/chat/completions": frozenset(
        {"messages", "tools", "tool_choice", "max_tokens", "max_completion_tokens"}
    ),
    "/responses": frozenset(
        {
            "input",
            "instructions",
            "tools",
            "tool_choice",
            "previous_response_id",
            "conversation",
            "include",
            "max_output_tokens",
        }
    ),
}
_TRACK_FORBIDDEN_FIELDS = frozenset(
    {
        "messages",
        "input",
        "instructions",
        "tools",
        "tool_choice",
        "previous_response_id",
        "conversation",
        "include",
        "model",
    }
)
_invalid_track_fields = _TRACK_FORBIDDEN_FIELDS.intersection(FORCE_PARAMETERS)
if _invalid_track_fields:
    invalid = ", ".join(sorted(_invalid_track_fields))
    raise RuntimeError(f"LLM_FORCE_PARAMETERS_JSON must not contain protocol fields: {invalid}")


def _model_track_digest() -> str:
    payload = {
        "model": FORCE_MODEL,
        "parameters": FORCE_PARAMETERS,
        "upstream_api": UPSTREAM_API,
        "upstream_timeout": UPSTREAM_TIMEOUT,
        "max_retries": MAX_RETRIES,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


MODEL_TRACK_DIGEST = _model_track_digest()

_token_log_lock = threading.Lock()
_upstream_call_lock = threading.Lock()
_upstream_calls = 0

# 每线程一个 httpx client（trust_env=False: relay 直连，不走容器代理变量）
_thread_local = threading.local()


def _client():
    import httpx

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


def _usage_get(usage: dict, *keys, default=None):
    for key in keys:
        if key in usage and usage[key] is not None:
            return usage[key]
    return default


def _append_token_log(
    model: str,
    call_type: str,
    usage: dict | None,
    duration: float,
    retries: int,
    *,
    stripped_params: tuple[str, ...] = (),
    response_kind: str | None = None,
) -> None:
    usage_available = isinstance(usage, dict)
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
        "model_track_digest": MODEL_TRACK_DIGEST,
        "effective_request_param_digest": MODEL_TRACK_DIGEST,
        "stripped_params": list(stripped_params),
        "response_kind": response_kind,
        "input_tokens": _usage_get(usage, "prompt_tokens", "input_tokens"),
        "output_tokens": _usage_get(usage, "completion_tokens", "output_tokens"),
        "cache_read_tokens": _usage_get(usage, "cache_read_input_tokens"),
        "cache_write_tokens": _usage_get(usage, "cache_creation_input_tokens"),
        "cached_tokens": prompt_details.get("cached_tokens"),
        "reasoning_tokens": completion_details.get("reasoning_tokens"),
        "usage_available": usage_available,
    }
    cache_components = (
        record["cache_read_tokens"],
        record["cache_write_tokens"],
        record["cached_tokens"],
    )
    record["cache_tokens"] = (
        sum(int(value) for value in cache_components)
        if all(value is not None for value in cache_components)
        else None
    )
    record["total_tokens"] = (
        int(record["input_tokens"]) + int(record["output_tokens"])
        if record["input_tokens"] is not None and record["output_tokens"] is not None
        else None
    )
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
    """Preserve protocol fields and inject only the frozen model track."""
    try:
        protocol_fields = _PROTOCOL_FIELDS[path]
    except KeyError as exc:
        raise RuntimeError(f"unsupported relay upstream path: {path}") from exc
    source = dict(body)
    body = {name: source[name] for name in protocol_fields if name in source}
    stripped = set(source).difference(protocol_fields | {"model"})
    _thread_local.stripped_params = tuple(sorted(stripped))
    body["model"] = FORCE_MODEL

    for name, value in FORCE_PARAMETERS.items():
        if name not in _CONTROL_PARAMETERS | {"reasoning_effort", "temperature"}:
            body[name] = value
    output_tokens = FORCE_PARAMETERS.get(
        "max_output_tokens",
        FORCE_PARAMETERS.get(
            "max_completion_tokens", FORCE_PARAMETERS.get("max_tokens")
        ),
    )
    if output_tokens is not None:
        body[
            "max_output_tokens" if path.endswith("/responses") else "max_tokens"
        ] = int(output_tokens)

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
    if TEMPERATURE and (
        path.endswith("/chat/completions") or path.endswith("/responses")
    ):
        body["temperature"] = float(TEMPERATURE)

    return body


def _request_telemetry() -> tuple[str, ...]:
    value = getattr(_thread_local, "stripped_params", ())
    return tuple(value) if isinstance(value, tuple) else ()


def _text_content(value: object) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return "" if value is None else str(value)
    parts: list[str] = []
    for block in value:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
            elif block.get("type") == "tool_result":
                parts.append(_text_content(block.get("content")))
    return "\n".join(part for part in parts if part)


def _messages_content_to_chat(value: object) -> object:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return _text_content(value)
    blocks: list[dict[str, object]] = []
    for block in value:
        if not isinstance(block, dict):
            blocks.append({"type": "text", "text": str(block)})
            continue
        block_type = block.get("type")
        if block_type == "text":
            blocks.append({"type": "text", "text": str(block.get("text", ""))})
        elif block_type == "image":
            source = block.get("source")
            if isinstance(source, dict) and source.get("type") == "base64":
                media_type = str(source.get("media_type", "application/octet-stream"))
                data = str(source.get("data", ""))
                blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{data}"},
                    }
                )
        elif block_type not in {"tool_use", "tool_result"}:
            text = _text_content(block)
            if text:
                blocks.append({"type": "text", "text": text})
    if not blocks:
        return ""
    if all(block.get("type") == "text" for block in blocks):
        return "\n".join(str(block.get("text", "")) for block in blocks)
    return blocks


def _messages_request_to_chat(body: dict) -> dict:
    chat: dict[str, object] = {"model": body.get("model", FORCE_MODEL)}
    messages: list[dict[str, object]] = []
    system = body.get("system")
    if system:
        messages.append({"role": "system", "content": _text_content(system)})
    for raw_message in body.get("messages", []):
        if not isinstance(raw_message, dict):
            continue
        role = str(raw_message.get("role", "user"))
        content = raw_message.get("content", "")
        if role == "assistant" and isinstance(content, list):
            tool_calls = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_calls.append(
                        {
                            "id": str(block.get("id", f"call_{uuid.uuid4().hex[:24]}")),
                            "type": "function",
                            "function": {
                                "name": str(block.get("name", "tool")),
                                "arguments": json.dumps(
                                    block.get("input", {}), ensure_ascii=False
                                ),
                            },
                        }
                    )
            message: dict[str, object] = {
                "role": "assistant",
                "content": _messages_content_to_chat(content),
            }
            if tool_calls:
                message["tool_calls"] = tool_calls
            messages.append(message)
            continue
        if role == "user" and isinstance(content, list):
            ordinary = [
                block
                for block in content
                if not isinstance(block, dict) or block.get("type") != "tool_result"
            ]
            if ordinary:
                messages.append(
                    {"role": "user", "content": _messages_content_to_chat(ordinary)}
                )
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": str(block.get("tool_use_id", "")),
                            "content": _text_content(block.get("content")),
                        }
                    )
            continue
        messages.append({"role": role, "content": _messages_content_to_chat(content)})
    chat["messages"] = messages
    if isinstance(body.get("tools"), list):
        chat["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": str(tool.get("name", "tool")),
                    "description": str(tool.get("description", "")),
                    "parameters": tool.get("input_schema", {}),
                },
            }
            for tool in body["tools"]
            if isinstance(tool, dict)
        ]
    tool_choice = body.get("tool_choice")
    if isinstance(tool_choice, dict):
        choice_type = tool_choice.get("type")
        if choice_type == "tool":
            chat["tool_choice"] = {
                "type": "function",
                "function": {"name": str(tool_choice.get("name", ""))},
            }
        elif choice_type == "any":
            chat["tool_choice"] = "required"
        elif choice_type in {"auto", "none"}:
            chat["tool_choice"] = choice_type
    for source, target in (
        ("max_tokens", "max_tokens"),
        ("stop_sequences", "stop"),
        ("temperature", "temperature"),
        ("top_p", "top_p"),
    ):
        if body.get(source) is not None:
            chat[target] = body[source]
    return chat


def _chat_content_to_responses(value: object, role: str) -> list[dict[str, object]]:
    text_type = "input_text"
    if isinstance(value, str):
        return [{"type": text_type, "text": value}]
    if not isinstance(value, list):
        return [{"type": text_type, "text": _text_content(value)}]
    output: list[dict[str, object]] = []
    for block in value:
        if not isinstance(block, dict):
            output.append({"type": text_type, "text": str(block)})
        elif block.get("type") == "text":
            output.append({"type": text_type, "text": str(block.get("text", ""))})
        elif block.get("type") == "image_url":
            image = block.get("image_url")
            if isinstance(image, dict):
                output.append({"type": "input_image", "image_url": image.get("url")})
    return output


def _chat_request_to_responses(body: dict) -> dict:
    response: dict[str, object] = {"model": body.get("model", FORCE_MODEL), "input": []}
    instructions: list[str] = []
    inputs: list[dict[str, object]] = []
    for message in body.get("messages", []):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "user"))
        if role in {"system", "developer"}:
            instructions.append(_text_content(message.get("content")))
            continue
        if role == "tool":
            inputs.append(
                {
                    "type": "function_call_output",
                    "call_id": str(message.get("tool_call_id", "")),
                    "output": _text_content(message.get("content")),
                }
            )
            continue
        content = message.get("content")
        if content is not None and content != "":
            inputs.append(
                {
                    "role": role,
                    "content": _chat_content_to_responses(content, role),
                }
            )
        if role == "assistant":
            for tool_call in message.get("tool_calls", []):
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function") or {}
                inputs.append(
                    {
                        "type": "function_call",
                        "call_id": str(tool_call.get("id", "")),
                        "name": str(function.get("name", "tool")),
                        "arguments": str(function.get("arguments", "{}")),
                    }
                )
    response["input"] = inputs
    if instructions:
        response["instructions"] = "\n\n".join(instructions)
    if isinstance(body.get("tools"), list):
        response["tools"] = [
            {
                "type": "function",
                "name": str((tool.get("function") or {}).get("name", "tool")),
                "description": str((tool.get("function") or {}).get("description", "")),
                "parameters": (tool.get("function") or {}).get("parameters", {}),
            }
            for tool in body["tools"]
            if isinstance(tool, dict)
        ]
    for name in ("temperature", "top_p", "parallel_tool_calls"):
        if body.get(name) is not None:
            response[name] = body[name]
    tool_choice = body.get("tool_choice")
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        function = tool_choice.get("function") or {}
        response["tool_choice"] = {
            "type": "function",
            "name": str(function.get("name", "")),
        }
    elif tool_choice is not None:
        response["tool_choice"] = tool_choice
    if body.get("max_tokens") is not None:
        response["max_output_tokens"] = body["max_tokens"]
    return response


def _responses_content_to_chat(value: object) -> object:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return _text_content(value)
    blocks: list[dict[str, object]] = []
    for block in value:
        if not isinstance(block, dict):
            blocks.append({"type": "text", "text": str(block)})
            continue
        block_type = block.get("type")
        if block_type in {"input_text", "output_text", "text"}:
            blocks.append({"type": "text", "text": str(block.get("text", ""))})
        elif block_type == "input_image":
            blocks.append(
                {
                    "type": "image_url",
                    "image_url": {"url": block.get("image_url")},
                }
            )
    if all(block.get("type") == "text" for block in blocks):
        return "\n".join(str(block.get("text", "")) for block in blocks)
    return blocks


def _responses_request_to_chat(body: dict) -> dict:
    chat: dict[str, object] = {"model": body.get("model", FORCE_MODEL)}
    messages: list[dict[str, object]] = []
    if body.get("instructions"):
        messages.append({"role": "system", "content": _text_content(body["instructions"])})
    raw_input = body.get("input", [])
    if isinstance(raw_input, str):
        messages.append({"role": "user", "content": raw_input})
    elif isinstance(raw_input, list):
        for item in raw_input:
            if not isinstance(item, dict):
                messages.append({"role": "user", "content": str(item)})
                continue
            item_type = item.get("type")
            if item_type == "function_call_output":
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(item.get("call_id", "")),
                        "content": _text_content(item.get("output")),
                    }
                )
            elif item_type == "function_call":
                messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": str(item.get("call_id", item.get("id", ""))),
                                "type": "function",
                                "function": {
                                    "name": str(item.get("name", "tool")),
                                    "arguments": str(item.get("arguments", "{}")),
                                },
                            }
                        ],
                    }
                )
            else:
                messages.append(
                    {
                        "role": str(item.get("role", "user")),
                        "content": _responses_content_to_chat(item.get("content", [])),
                    }
                )
    chat["messages"] = messages
    if isinstance(body.get("tools"), list):
        chat["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": str(tool.get("name", "tool")),
                    "description": str(tool.get("description", "")),
                    "parameters": tool.get("parameters", {}),
                },
            }
            for tool in body["tools"]
            if isinstance(tool, dict)
        ]
    for name in ("temperature", "top_p", "parallel_tool_calls"):
        if body.get(name) is not None:
            chat[name] = body[name]
    tool_choice = body.get("tool_choice")
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        chat["tool_choice"] = {
            "type": "function",
            "function": {"name": str(tool_choice.get("name", ""))},
        }
    elif tool_choice is not None:
        chat["tool_choice"] = tool_choice
    if body.get("max_output_tokens") is not None:
        chat["max_tokens"] = body["max_output_tokens"]
    return chat


# --- Truncation / stop-signal propagation across protocol conversions -------
#
# Each wire protocol names the same terminal states differently.  A relay that
# rewrites one protocol into another must carry the *incomplete* states across,
# otherwise an Agent whose answer was cut off by an upstream output-token limit
# receives an ordinary "stop"/"end_turn" and treats a truncated answer as a
# complete one.

# Responses API `incomplete_details.reason` -> chat `finish_reason`.
_RESPONSES_INCOMPLETE_REASONS = {
    "max_output_tokens": "length",
    "max_tokens": "length",
    "content_filter": "content_filter",
}


def _responses_finish_reason(data: dict, *, has_tool_calls: bool) -> str:
    """Map a Responses API terminal state onto an OpenAI chat finish_reason."""

    status = data.get("status")
    details = data.get("incomplete_details")
    reason = details.get("reason") if isinstance(details, dict) else None
    if status == "incomplete" or reason is not None:
        return _RESPONSES_INCOMPLETE_REASONS.get(str(reason), "length")
    # Some upstreams report the truncation on the message item instead of on the
    # response envelope.
    for item in data.get("output", []):
        if isinstance(item, dict) and item.get("status") == "incomplete":
            return "length"
    return "tool_calls" if has_tool_calls else "stop"


def _chat_finish_reason(data: dict) -> str:
    choice = (data.get("choices") or [{}])[0]
    reason = choice.get("finish_reason")
    if isinstance(reason, str) and reason:
        return reason
    message = choice.get("message") or {}
    return "tool_calls" if message.get("tool_calls") else "stop"


# Chat `finish_reason` -> Responses API (status, incomplete_details).
def _chat_finish_reason_to_responses_status(reason: str) -> tuple[str, dict | None]:
    if reason == "length":
        return "incomplete", {"reason": "max_output_tokens"}
    if reason == "content_filter":
        return "incomplete", {"reason": "content_filter"}
    return "completed", None


# Chat `finish_reason` -> Anthropic Messages API `stop_reason`.
_CHAT_TO_MESSAGES_STOP_REASON = {
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "refusal",
    "stop": "end_turn",
}


def _messages_stop_reason(data: dict) -> str:
    reason = _chat_finish_reason(data)
    return _CHAT_TO_MESSAGES_STOP_REASON.get(reason, "end_turn")


def _responses_response_to_chat(data: dict) -> dict:
    texts: list[str] = []
    tool_calls: list[dict[str, object]] = []
    for item in data.get("output", []):
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for block in item.get("content", []):
                if isinstance(block, dict) and block.get("type") in {
                    "output_text",
                    "text",
                    "refusal",
                }:
                    text = block.get("text", block.get("refusal"))
                    if isinstance(text, str):
                        texts.append(text)
        elif item.get("type") == "function_call":
            tool_calls.append(
                {
                    "id": str(item.get("call_id", item.get("id", ""))),
                    "type": "function",
                    "function": {
                        "name": str(item.get("name", "tool")),
                        "arguments": str(item.get("arguments", "{}")),
                    },
                }
            )
    message: dict[str, object] = {
        "role": "assistant",
        "content": "\n".join(texts) if texts else None,
    }
    if tool_calls:
        message["tool_calls"] = tool_calls
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return {
        "id": str(data.get("id", f"chatcmpl-{uuid.uuid4().hex[:24]}")),
        "object": "chat.completion",
        "created": int(data.get("created_at", time.time())),
        "model": data.get("model", FORCE_MODEL),
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": _responses_finish_reason(
                    data, has_tool_calls=bool(tool_calls)
                ),
            }
        ],
        "usage": {
            "prompt_tokens": usage.get("input_tokens"),
            "completion_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
        },
    }


def _chat_response_to_responses(data: dict) -> dict:
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    output: list[dict[str, object]] = []
    text = message.get("content")
    if isinstance(text, str) and text:
        output.append(
            {
                "id": f"msg_{uuid.uuid4().hex[:24]}",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                        "annotations": [],
                    }
                ],
            }
        )
    for tool_call in message.get("tool_calls", []):
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function") or {}
        output.append(
            {
                "id": str(tool_call.get("id", f"fc_{uuid.uuid4().hex[:24]}")),
                "call_id": str(tool_call.get("id", f"call_{uuid.uuid4().hex[:24]}")),
                "type": "function_call",
                "status": "completed",
                "name": str(function.get("name", "tool")),
                "arguments": str(function.get("arguments", "{}")),
            }
        )
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    response_id = str(data.get("id", f"resp_{uuid.uuid4().hex[:24]}"))
    if not response_id.startswith("resp_"):
        response_id = f"resp_{response_id}"
    status, incomplete_details = _chat_finish_reason_to_responses_status(
        _chat_finish_reason(data)
    )
    if status != "completed":
        for item in output:
            item["status"] = "incomplete"
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(data.get("created", time.time())),
        "status": status,
        "error": None,
        "incomplete_details": incomplete_details,
        "model": data.get("model", FORCE_MODEL),
        "output": output,
        "output_text": text or "",
        "parallel_tool_calls": True,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", usage.get("input_tokens")),
            "output_tokens": usage.get("completion_tokens", usage.get("output_tokens")),
            "total_tokens": usage.get("total_tokens"),
        },
    }


def _chat_response_to_messages(data: dict) -> dict:
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content: list[dict[str, object]] = []
    if isinstance(message.get("content"), str) and message["content"]:
        content.append({"type": "text", "text": message["content"]})
    for tool_call in message.get("tool_calls", []):
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function") or {}
        try:
            arguments = json.loads(str(function.get("arguments", "{}")))
        except json.JSONDecodeError:
            arguments = {"raw": str(function.get("arguments", ""))}
        content.append(
            {
                "type": "tool_use",
                "id": str(tool_call.get("id", f"call_{uuid.uuid4().hex[:24]}")),
                "name": str(function.get("name", "tool")),
                "input": arguments,
            }
        )
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return {
        "id": str(data.get("id", f"msg_{uuid.uuid4().hex[:24]}")),
        "type": "message",
        "role": "assistant",
        "model": data.get("model", FORCE_MODEL),
        "content": content,
        "stop_reason": _messages_stop_reason(data),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", usage.get("input_tokens")),
            "output_tokens": usage.get("completion_tokens", usage.get("output_tokens")),
        },
    }


def _post_canonical_chat(body: dict, call_type: str) -> tuple[dict, float, int]:
    """Forward a chat-shaped request to the upstream chat endpoint.

    Cross-protocol rewriting used to be decided by a single global switch, which
    could only ever be right for half the fleet: translating an Agent's native
    wire format loses whatever the target format cannot express. Codex declares
    its tools in a `additional_tools` input item that has no chat equivalent, so
    downgrading its /responses call dropped every tool and it reported having no
    shell; converting MLEvolve's chat calls the other way made its first real
    request hang for ten minutes. Each Agent now reaches upstream on the protocol
    it actually speaks. Only `_rewrite_body` still intervenes, and only to pin the
    frozen model track.
    """
    if UPSTREAM_API == "responses" and _FORCE_CROSS_PROTOCOL:
        data, duration, retries = _post_upstream(
            "/responses",
            _rewrite_body(_chat_request_to_responses(body), "/responses"),
            call_type,
        )
        return _responses_response_to_chat(data), duration, retries
    return _post_upstream(
        "/chat/completions",
        _rewrite_body(body, "/chat/completions"),
        call_type,
    )


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
            global _upstream_calls
            with _upstream_call_lock:
                if (
                    MAX_UPSTREAM_CALLS is not None
                    and _upstream_calls >= MAX_UPSTREAM_CALLS
                ):
                    raise _UpstreamCallLimitError(
                        f"relay upstream call limit reached: {MAX_UPSTREAM_CALLS}"
                    )
                _upstream_calls += 1
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
        except _UpstreamCallLimitError:
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


class _UpstreamCallLimitError(Exception):
    pass


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


def _tool_choice_requires_call(value: object) -> bool:
    """Whether the downstream protocol explicitly required a tool call."""
    if value == "required":
        return True
    if not isinstance(value, dict):
        return False
    return value.get("type") in {"function", "tool", "required", "any"}


class _ToolCallProtocolError(Exception):
    """The upstream ignored an explicitly required function call."""


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


def _synthesize_responses_sse(data: dict) -> bytes:
    """Convert one complete Responses API object into Codex-compatible SSE."""

    events = [
        {
            "type": "response.output_item.done",
            "item": item,
        }
        for item in data.get("output", [])
        if isinstance(item, dict)
    ]
    # A truncated response must not be announced as `response.completed`, or a
    # Responses-API client treats the cut-off output as a finished answer.
    terminal = (
        "response.incomplete"
        if data.get("status") == "incomplete"
        else "response.completed"
    )
    events.append({"type": terminal, "response": data})
    return "".join(
        f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        for event in events
    ).encode("utf-8")


def _synthesize_messages_sse(data: dict) -> bytes:
    events: list[tuple[str, dict[str, object]]] = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {**data, "content": [], "stop_reason": None},
            },
        )
    ]
    for index, block in enumerate(data.get("content", [])):
        if not isinstance(block, dict):
            continue
        events.append(
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": (
                        {"type": "text", "text": ""}
                        if block.get("type") == "text"
                        else {
                            "type": "tool_use",
                            "id": block.get("id"),
                            "name": block.get("name"),
                            "input": {},
                        }
                    ),
                },
            )
        )
        delta = (
            {"type": "text_delta", "text": block.get("text", "")}
            if block.get("type") == "text"
            else {
                "type": "input_json_delta",
                "partial_json": json.dumps(block.get("input", {}), ensure_ascii=False),
            }
        )
        events.append(
            (
                "content_block_delta",
                {"type": "content_block_delta", "index": index, "delta": delta},
            )
        )
        events.append(
            (
                "content_block_stop",
                {"type": "content_block_stop", "index": index},
            )
        )
    events.extend(
        (
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": data.get("stop_reason")},
                    "usage": {"output_tokens": data.get("usage", {}).get("output_tokens")},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        )
    )
    return "".join(
        f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        for name, payload in events
    ).encode("utf-8")


class ThreadingUnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


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

    def _authorized(self) -> bool:
        expected = f"Bearer {INBOUND_API_KEY}"
        if (
            self.headers.get("Authorization") == expected
            or self.headers.get("x-api-key") == INBOUND_API_KEY
            or self.headers.get("api-key") == INBOUND_API_KEY
        ):
            return True
        self._send_json(401, {"error": {"message": "invalid relay credential"}})
        return False

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            if not self._authorized():
                return
            model = FORCE_MODEL
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
        if not self._authorized():
            return
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
            elif path == "/responses":
                self._handle_responses(body)
            elif path == "/messages":
                self._handle_messages(body)
            else:
                raise ValueError(f"unsupported downstream endpoint: {path}")
        except _ToolCallProtocolError as exc:
            self._send_json(
                502,
                {
                    "error": {
                        "type": "tool_call_protocol_error",
                        "message": str(exc),
                    }
                },
            )
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
        has_tools = bool(body.get("tools"))
        model = FORCE_MODEL or body.get("model", "")

        data, duration, retries = _post_canonical_chat(body, "chat.completions")

        message = ((data.get("choices") or [{}])[0].get("message")) or {}
        tool_calls = message.get("tool_calls") or []
        response_kind = "tool_call" if tool_calls else "text"
        stripped_params = _request_telemetry()
        if has_tools and _tool_choice_requires_call(body.get("tool_choice")) and not tool_calls:
            _append_token_log(
                model,
                "chat.completions.protocol_error",
                data.get("usage"),
                duration,
                retries,
                stripped_params=stripped_params,
                response_kind="text_required_tool_call_missing",
            )
            raise _ToolCallProtocolError(
                "upstream returned text without tool_calls for an explicitly required tool call"
            )

        _append_token_log(
            model,
            "chat.completions" + (".stream" if client_wants_stream else ""),
            data.get("usage"),
            duration,
            retries,
            stripped_params=stripped_params,
            response_kind=response_kind,
        )

        if client_wants_stream:
            self._send_json(200, _synthesize_sse(data), content_type="text/event-stream")
        else:
            self._send_json(200, data)

    def _handle_responses(self, body: dict) -> None:
        client_wants_stream = bool(body.pop("stream", False))
        body.pop("stream_options", None)
        model = FORCE_MODEL or body.get("model", "")
        # Native by default: a /responses client reaches the /responses endpoint,
        # so tool declarations the chat schema cannot carry survive untouched.
        if UPSTREAM_API == "responses" or not _FORCE_CROSS_PROTOCOL:
            data, duration, retries = _post_upstream(
                "/responses",
                _rewrite_body(body, "/responses"),
                "responses",
            )
        else:
            chat, duration, retries = _post_canonical_chat(
                _responses_request_to_chat(body), "responses.via_chat"
            )
            data = _chat_response_to_responses(chat)
        _append_token_log(
            model,
            "responses" + (".stream" if client_wants_stream else ""),
            data.get("usage"),
            duration,
            retries,
            stripped_params=_request_telemetry(),
            response_kind="responses",
        )
        if client_wants_stream:
            self._send_json(
                200,
                _synthesize_responses_sse(data),
                content_type="text/event-stream",
            )
        else:
            self._send_json(200, data)

    def _handle_messages(self, body: dict) -> None:
        client_wants_stream = bool(body.pop("stream", False))
        model = FORCE_MODEL or body.get("model", "")
        chat, duration, retries = _post_canonical_chat(
            _messages_request_to_chat(body), "messages"
        )
        data = _chat_response_to_messages(chat)
        _append_token_log(
            model,
            "messages" + (".stream" if client_wants_stream else ""),
            data.get("usage"),
            duration,
            retries,
            stripped_params=_request_telemetry(),
            response_kind="messages",
        )
        if client_wants_stream:
            self._send_json(
                200,
                _synthesize_messages_sse(data),
                content_type="text/event-stream",
            )
        else:
            self._send_json(200, data)

def main() -> None:
    parser = argparse.ArgumentParser(description="LLM relay proxy")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6200)
    parser.add_argument("--unix-socket", type=Path)
    args = parser.parse_args()

    if (
        not UPSTREAM_BASE_URL
        or not FORCE_MODEL
        or not FORCE_PARAMETERS
        or _max_retries_raw is None
    ):
        raise RuntimeError(
            "UPSTREAM_BASE_URL, LLM_FORCE_MODEL, and non-empty "
            "LLM_FORCE_PARAMETERS_JSON plus explicit LLM_MAX_RETRIES are required"
        )

    if not UPSTREAM_API_KEY:
        logger.warning("UPSTREAM_API_KEY / OPENAI_API_KEY 未设置，上游调用将失败")

    if args.unix_socket is None:
        server = ThreadingHTTPServer((args.host, args.port), ProxyHandler)
        listen_address = f"{args.host}:{args.port}"
    else:
        args.unix_socket.parent.mkdir(parents=True, exist_ok=True)
        args.unix_socket.unlink(missing_ok=True)
        server = ThreadingUnixHTTPServer(str(args.unix_socket), ProxyHandler)
        listen_address = str(args.unix_socket)
    logger.info(
        "listening on %s → %s | model=%s track=%s effort=%s timeout=%s retries=%s agent=%s",
        listen_address, UPSTREAM_BASE_URL, FORCE_MODEL or "(passthrough)",
        MODEL_TRACK_DIGEST[:16], REASONING_EFFORT or "(none)", UPSTREAM_TIMEOUT,
        MAX_RETRIES, AGENT_NAME,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if args.unix_socket is not None:
            args.unix_socket.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
