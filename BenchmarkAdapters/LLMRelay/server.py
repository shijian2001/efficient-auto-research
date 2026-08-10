#!/usr/bin/env python
"""Repository-owned LLM relay server for every benchmark Agent request.

Agent 侧只需把 OPENAI_BASE_URL 指到本代理 (http://127.0.0.1:<port>/v1)，
上游适配逻辑全部集中在这里，agent 代码保持纯上游原版：

  - 下游协议:      接受 OpenAI Chat、OpenAI Responses 和 Messages
  - 上游协议:      只发送 OpenAI-compatible Chat 或 Responses
  - 模型重写:      请求里的任何 model 统一改写为必填 LLM_FORCE_MODEL
  - 推理参数:      仅注入 LLM_FORCE_PARAMETERS_JSON 中明确配置的参数
  - 参数清洗:      剥掉 max_tokens / max_completion_tokens / web_search_options 等
                   relay 不支持或不应限制的参数 (LLM_STRIP_PARAMS 可配)
  - 超时:          上游请求默认不设超时 (LLM_UPSTREAM_TIMEOUT 可配秒数)
  - 重试:          连接错误 / 429 / 5xx / 空响应按显式 LLM_MAX_RETRIES 配置重试
  - 非流式化:      agent 请求 stream=True 时，上游走非流式，拿到完整结果后
                   以 SSE 格式一次性回给 agent (规避 relay 掉 chunk 问题)
  - tool 调用兜底: 请求带 tools 但上游没返回 tool_calls 时，自动改写为
                   JSON 文本模式重试，把解析出的 JSON 伪装成 tool_call 响应
  - token 记录:    每次上游调用的 usage 追加写入 LLM_TOKEN_LOG_PATH (jsonl)

用法:
  python -m BenchmarkAdapters.LLMRelay.server --port 6200 [--host 127.0.0.1]

环境变量:
  UPSTREAM_BASE_URL     上游 API 地址（必填）
  UPSTREAM_API_KEY      上游 API key (必填, 或复用 OPENAI_API_KEY)
  LLM_FORCE_MODEL       统一改写的模型名（必填）
  LLM_FORCE_PARAMETERS_JSON  统一模型参数 JSON 对象（必填且不可为空）
  LLM_STRIP_PARAMS      逗号分隔的待剥参数 (默认 max_tokens,max_completion_tokens,web_search_options)
  LLM_UPSTREAM_TIMEOUT  上游超时秒数 (默认空 = 不限)
  LLM_UPSTREAM_PROXY    上游 HTTP(S) 代理 (默认空 = 直连)
  LLM_MAX_RETRIES       上游重试次数（必需由 launcher/model track 显式注入）
  LLM_UPSTREAM_API      chat 或 responses（默认 chat）
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
    FORCE_PARAMETERS = json.loads(os.environ.get("LLM_FORCE_PARAMETERS_JSON", "{}"))
except json.JSONDecodeError as exc:
    raise RuntimeError("LLM_FORCE_PARAMETERS_JSON is invalid") from exc
if not isinstance(FORCE_PARAMETERS, dict):
    raise RuntimeError("LLM_FORCE_PARAMETERS_JSON must be an object")
REASONING_EFFORT = str(FORCE_PARAMETERS.get("reasoning_effort", ""))
TEMPERATURE = str(FORCE_PARAMETERS.get("temperature", "")).strip()
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
_max_retries_raw = os.environ.get("LLM_MAX_RETRIES")
MAX_RETRIES = int(_max_retries_raw) + 1 if _max_retries_raw is not None else 0
AGENT_NAME = os.environ.get("LLM_PROXY_AGENT_NAME", "unknown")
INBOUND_API_KEY = os.environ.get("LLM_PROXY_API_KEY", "proxy")
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

_token_log_lock = threading.Lock()

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


def _append_token_log(model: str, call_type: str, usage: dict | None, duration: float, retries: int) -> None:
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
    """模型重写 + 推理参数注入 + 参数清洗 + 消息归一化。"""
    body = dict(body)
    if FORCE_MODEL:
        body["model"] = FORCE_MODEL

    for param in (
        *STRIP_PARAMS,
        *_CONTROL_PARAMETERS,
        "temperature",
        "reasoning_effort",
        "reasoning",
    ):
        body.pop(param, None)
    incompatible_sampling_parameters = (
        {"logprobs", "top_logprobs", "top_p"}
        if REASONING_EFFORT and REASONING_EFFORT != "none"
        else set()
    )
    for param in incompatible_sampling_parameters:
        body.pop(param, None)

    for name, value in FORCE_PARAMETERS.items():
        if (
            name not in _CONTROL_PARAMETERS | {"reasoning_effort", "temperature"}
            and name not in incompatible_sampling_parameters
        ):
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
                "finish_reason": "tool_calls" if tool_calls else "stop",
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
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(data.get("created", time.time())),
        "status": "completed",
        "error": None,
        "incomplete_details": None,
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
        "stop_reason": "tool_use" if message.get("tool_calls") else "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", usage.get("input_tokens")),
            "output_tokens": usage.get("completion_tokens", usage.get("output_tokens")),
        },
    }


def _post_canonical_chat(body: dict, call_type: str) -> tuple[dict, float, int]:
    if UPSTREAM_API == "responses":
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

    data, duration, retries = _post_canonical_chat(
        fallback, "chat.tool_json_fallback"
    )
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
    events.append({"type": "response.completed", "response": data})
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

    def _handle_responses(self, body: dict) -> None:
        client_wants_stream = bool(body.pop("stream", False))
        body.pop("stream_options", None)
        model = FORCE_MODEL or body.get("model", "")
        if UPSTREAM_API == "responses":
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
        "listening on %s → %s | model=%s effort=%s strip=%s timeout=%s retries=%s agent=%s",
        listen_address, UPSTREAM_BASE_URL, FORCE_MODEL or "(passthrough)",
        REASONING_EFFORT or "(none)", ",".join(STRIP_PARAMS), UPSTREAM_TIMEOUT, MAX_RETRIES, AGENT_NAME,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if args.unix_socket is not None:
            args.unix_socket.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
