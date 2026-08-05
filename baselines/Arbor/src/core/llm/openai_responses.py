"""OpenAI Responses provider with reasoning and function calling support."""

# pylint: disable=broad-exception-caught

from __future__ import annotations

import copy
import json
import logging
import os
from typing import Any

import tiktoken
from openai import AsyncOpenAI

from .base import (
    ContentBlock,
    LLMProvider,
    LLMResponse,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    Usage,
    make_tool_use_id,
)

log = logging.getLogger(__name__)


class OpenAIResponsesProvider(LLMProvider):
    """LLM provider backed by OpenAI's Responses API.

    This is the preferred path for GPT-5/o-series reasoning models because it
    exposes ``reasoning`` controls and native ``function_call`` items.
    """

    def __init__(
        self,
        model: str = "gpt-5.5",
        api_key: str | None = None,
        base_url: str | None = None,
        max_retries: int = 3,
        timeout: float = 300.0,
        reasoning_effort: str | None = "high",
        reasoning_summary: str | None = "auto",
        text_verbosity: str | None = "medium",
        parallel_tool_calls: bool | None = True,
    ):
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self.reasoning_effort = reasoning_effort
        self.reasoning_summary = reasoning_summary
        self.text_verbosity = text_verbosity
        self.parallel_tool_calls = parallel_tool_calls
        self._client = AsyncOpenAI(
            api_key=api_key or os.environ.get("OPENAI_API_KEY", "dummy"),
            base_url=base_url,
            max_retries=max_retries,
            timeout=timeout,
        )
        try:
            self._enc = tiktoken.encoding_for_model(model)
        except Exception:
            self._enc = tiktoken.get_encoding("cl100k_base")

    async def create(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 16384,
    ) -> LLMResponse:
        params = self._build_request_params(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens,
        )
        raw = await self._client.responses.create(**params)
        return self._parse_response(raw)

    def _build_request_params(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
    ) -> dict[str, Any]:
        input_items = self._convert_messages(messages)

        params: dict[str, Any] = {
            "model": self.model,
            "instructions": system,
            "input": input_items,
            "max_output_tokens": max_tokens,
            # We replay full context each turn, so server-side response state is unnecessary.
            "store": False,
        }

        reasoning = self._build_reasoning_config()
        if reasoning:
            params["reasoning"] = reasoning
            # Stateless multi-turn: with ``store=False`` the only way to keep the
            # reasoning chain across ReAct turns is to have the model return its
            # encrypted reasoning payload and replay it next turn. Without this,
            # replayed reasoning items carry only a summary + a stale id and the
            # API rejects them ("item not found") — the chain silently breaks.
            params["include"] = ["reasoning.encrypted_content"]

        text = self._build_text_config()
        if text:
            params["text"] = text

        if tools:
            params["tools"] = self._convert_tools(tools)
            params["tool_choice"] = "auto"
            if self.parallel_tool_calls is not None:
                params["parallel_tool_calls"] = self.parallel_tool_calls

        return params

    def count_tokens(self, text: str) -> int:
        return len(self._enc.encode(text))

    def _build_reasoning_config(self) -> dict[str, Any] | None:
        reasoning: dict[str, Any] = {}
        if self.reasoning_effort is not None:
            reasoning["effort"] = self.reasoning_effort
        if self.reasoning_summary is not None:
            reasoning["summary"] = self.reasoning_summary
        return reasoning or None

    def _build_text_config(self) -> dict[str, Any] | None:
        if self.text_verbosity is None:
            return None
        return {"verbosity": self.text_verbosity}

    def _convert_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert the framework's message history to Responses input items."""
        items: list[dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")

            if isinstance(content, str):
                items.append({"role": role, "content": content})
                continue

            if not isinstance(content, list):
                items.append({"role": role, "content": str(content)})
                continue

            if role == "assistant":
                items.extend(self._assistant_blocks_to_items(content))
            elif role == "user":
                items.extend(self._user_blocks_to_items(content))
            else:
                items.append({"role": role, "content": self._blocks_to_text(content)})

        return items

    def _assistant_blocks_to_items(self, content: list[Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        text_parts: list[str] = []

        for block in content:
            if not isinstance(block, dict):
                text_parts.append(str(block))
                continue

            block_type = block.get("type")
            if block_type == "reasoning":
                # Replay the reasoning item ONLY if it carries the encrypted
                # payload (requested via include=[...]). A summary-only item, or
                # one whose id references a non-stored response, is rejected by
                # the API under store=False — so drop it rather than break the
                # whole request. (Summaries are display-only; see #6.)
                if block.get("encrypted_content"):
                    items.append(copy.deepcopy(block))
                continue
            if block_type in {
                "message",
                "function_call",
                "function_call_output",
                "custom_tool_call",
                "tool_search_call",
                "tool_search_output",
            }:
                items.append(copy.deepcopy(block))
            elif block_type == "text":
                text = block.get("text", "")
                if text:
                    text_parts.append(text)
            elif block_type == "thinking":
                # Cross-provider reasoning (no encrypted payload) cannot be
                # replayed to the Responses API — keep it for display only.
                continue
            elif block_type == "tool_use":
                items.append({
                    "type": "function_call",
                    "call_id": block.get("call_id") or block.get("id") or make_tool_use_id(),
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                })

        if text_parts:
            items.insert(0, {
                "role": "assistant",
                "content": "\n".join(text_parts),
            })

        return items

    def _user_blocks_to_items(self, content: list[Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        text_parts: list[str] = []

        for block in content:
            if not isinstance(block, dict):
                text_parts.append(str(block))
                continue

            block_type = block.get("type")
            if block_type == "tool_result":
                items.append({
                    "type": "function_call_output",
                    "call_id": block.get("tool_use_id", ""),
                    "output": block.get("content", ""),
                })
            elif block_type == "text":
                text = block.get("text", "")
                if text:
                    text_parts.append(text)
            elif block_type == "function_call_output":
                items.append(copy.deepcopy(block))

        if text_parts:
            items.insert(0, {"role": "user", "content": "\n".join(text_parts)})

        return items

    @staticmethod
    def _blocks_to_text(content: list[Any]) -> str:
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                else:
                    parts.append(json.dumps(block, ensure_ascii=False))
            else:
                parts.append(str(block))
        return "\n".join(p for p in parts if p)

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        response_tools: list[dict[str, Any]] = []
        for tool in tools:
            response_tools.append({
                "type": "function",
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
                # Preserve existing best-effort schemas; Responses may default to strict.
                "strict": False,
            })
        return response_tools

    @classmethod
    def _to_python(cls, value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, list):
            return [cls._to_python(item) for item in value]
        if isinstance(value, tuple):
            return [cls._to_python(item) for item in value]
        if isinstance(value, dict):
            return {k: cls._to_python(v) for k, v in value.items()}

        for method_name in ("model_dump", "to_dict", "dict"):
            method = getattr(value, method_name, None)
            if callable(method):
                try:
                    return cls._to_python(method(exclude_none=True))
                except TypeError:
                    return cls._to_python(method())

        if hasattr(value, "__dict__"):
            return {
                key: cls._to_python(val)
                for key, val in vars(value).items()
                if not key.startswith("_")
            }

        return value

    @staticmethod
    def _content_part_to_text(part: Any) -> str:
        if part is None:
            return ""
        if isinstance(part, str):
            return part
        if isinstance(part, dict):
            if isinstance(part.get("text"), str):
                return part["text"]
            if isinstance(part.get("content"), str):
                return part["content"]
        return str(part)

    @classmethod
    def _message_text(cls, item: dict[str, Any]) -> str:
        content = item.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(cls._content_part_to_text(part) for part in content).strip()
        return "" if content is None else str(content)

    @classmethod
    def _reasoning_text(cls, item: dict[str, Any]) -> str:
        for field in ("summary", "content"):
            value = item.get(field)
            if isinstance(value, list):
                text = "".join(cls._content_part_to_text(part) for part in value).strip()
                if text:
                    return text
            elif isinstance(value, str) and value.strip():
                return value.strip()
        text = item.get("text")
        return text.strip() if isinstance(text, str) else ""

    @staticmethod
    def _parse_arguments(arguments: Any) -> dict[str, Any]:
        if isinstance(arguments, dict):
            return arguments
        if not arguments:
            return {}
        if not isinstance(arguments, str):
            return {}
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _parse_response(self, raw: Any) -> LLMResponse:
        output_items = [
            item for item in self._to_python(getattr(raw, "output", [])) or []
            if isinstance(item, dict)
        ]

        content: list[ContentBlock] = []
        text_parts: list[str] = []
        tool_calls: list[ToolUseBlock] = []

        for item in output_items:
            item_type = item.get("type")
            if item_type == "reasoning":
                text = self._reasoning_text(item)
                if text:
                    content.append(ThinkingBlock(text=text))
            elif item_type == "message":
                text = self._message_text(item)
                if text:
                    text_parts.append(text)
            elif item_type == "function_call":
                call_id = item.get("call_id") or item.get("id") or make_tool_use_id()
                tool_calls.append(ToolUseBlock(
                    id=call_id,
                    name=item.get("name", ""),
                    input=self._parse_arguments(item.get("arguments")),
                ))

        if text_parts:
            # Some OpenAI-compatible proxies emit the same assistant text as
            # more than one `message` output item, which would otherwise render
            # twice. Collapse consecutive identical blocks; a conformant
            # Responses API returns a single message item and is unaffected.
            deduped: list[str] = []
            for part in text_parts:
                if not deduped or deduped[-1] != part:
                    deduped.append(part)
            content.append(TextBlock(text="\n\n".join(deduped).strip()))
        content.extend(tool_calls)

        if not text_parts and not tool_calls:
            output_text = getattr(raw, "output_text", "") or ""
            if output_text:
                content.append(TextBlock(text=output_text))

        usage = Usage()
        raw_usage = getattr(raw, "usage", None)
        if raw_usage:
            total_input = int(getattr(raw_usage, "input_tokens", 0) or 0)
            usage.output_tokens = int(getattr(raw_usage, "output_tokens", 0) or 0)
            # Responses counts cached prompt tokens inside ``input_tokens`` (under
            # ``input_tokens_details.cached_tokens``); split them out so cache hits
            # are visible and ``total_input_tokens`` doesn't double-count.
            details = getattr(raw_usage, "input_tokens_details", None)
            cached = getattr(details, "cached_tokens", None) if details is not None else None
            if cached is None and isinstance(details, dict):
                cached = details.get("cached_tokens")
            cached = int(cached or 0)
            usage.input_tokens = max(0, total_input - cached)
            usage.cache_read_tokens = cached

        stop_reason = "tool_use" if tool_calls else "end_turn"
        if getattr(raw, "status", None) == "incomplete":
            details = getattr(raw, "incomplete_details", None)
            reason = getattr(details, "reason", "") if details else ""
            if reason == "max_output_tokens":
                stop_reason = "max_tokens"

        return LLMResponse(
            content=content,
            stop_reason=stop_reason,
            usage=usage,
            model=getattr(raw, "model", None) or self.model,
            raw_content=copy.deepcopy(output_items),
        )
