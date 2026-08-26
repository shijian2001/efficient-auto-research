"""Unified types and abstract base for LLM providers."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Union


# ---------------------------------------------------------------------------
# Unified content block types
# ---------------------------------------------------------------------------

@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class ThinkingBlock:
    """Reasoning / chain-of-thought content from the model."""
    text: str
    signature: str = ""  # Anthropic uses this; OpenAI-compat backends usually don't
    type: str = "thinking"


ContentBlock = Union[TextBlock, ToolUseBlock, ThinkingBlock]


@dataclass
class ToolCall:
    """Extracted tool call from an LLM response."""
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    @property
    def total_input_tokens(self) -> int:
        """Logical input tokens for this call, including Anthropic cache hits.

        Anthropic reports cache reads/creations outside ``input_tokens``.
        OpenAI-style providers generally include cached tokens in
        ``prompt_tokens`` already, so they should leave the cache fields at 0.
        """
        return (
            int(self.input_tokens or 0)
            + int(self.cache_read_tokens or 0)
            + int(self.cache_creation_tokens or 0)
        )

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + int(self.output_tokens or 0)


@dataclass
class LLMResponse:
    """Provider-agnostic LLM response."""
    content: list[ContentBlock]
    stop_reason: str  # "end_turn" | "tool_use" | "max_tokens"
    usage: Usage = field(default_factory=Usage)
    model: str = ""

    # Raw content list in the provider's native format for message history.
    # Populated by the provider so that appending to messages[] is lossless.
    raw_content: list[dict[str, Any]] = field(default_factory=list)

    # Sampled-token logprobs when the provider returns them (OpenAI logprobs=true);
    # None otherwise (e.g. Anthropic). For token-faithful SFT/RL traces.
    logprobs: list[dict[str, Any]] | None = None

    def get_text(self) -> str:
        """Concatenate all text blocks."""
        return "\n".join(b.text for b in self.content if isinstance(b, TextBlock))

    def get_tool_calls(self) -> list[ToolCall]:
        """Extract tool calls from content."""
        return [
            ToolCall(id=b.id, name=b.name, input=b.input)
            for b in self.content
            if isinstance(b, ToolUseBlock)
        ]

    @property
    def has_tool_use(self) -> bool:
        return any(isinstance(b, ToolUseBlock) for b in self.content)


@dataclass
class ToolResultBlock:
    """A single tool result to feed back to the LLM."""
    tool_use_id: str
    content: str
    is_error: bool = False

    def to_content_block(self) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "tool_use_id": self.tool_use_id,
            "content": self.content,
            **({"is_error": True} if self.is_error else {}),
        }


# ---------------------------------------------------------------------------
# Stream events (for streaming mode)
# ---------------------------------------------------------------------------

@dataclass
class StreamTextDelta:
    text: str
    type: str = "text_delta"


@dataclass
class StreamThinkingDelta:
    text: str
    type: str = "thinking_delta"


@dataclass
class StreamToolStart:
    id: str
    name: str
    type: str = "tool_start"


@dataclass
class StreamToolDelta:
    partial_json: str
    type: str = "tool_delta"


@dataclass
class StreamDone:
    response: LLMResponse
    type: str = "done"


StreamEvent = Union[StreamTextDelta, StreamThinkingDelta, StreamToolStart, StreamToolDelta, StreamDone]


# ---------------------------------------------------------------------------
# Abstract provider
# ---------------------------------------------------------------------------

class LLMProvider(ABC):
    """Abstract base class for LLM providers.

    All providers must normalise their responses into the unified types above.
    """

    model: str

    @abstractmethod
    async def create(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 16384,
    ) -> LLMResponse:
        """Single-shot (non-streaming) completion."""

    async def create_streaming(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 16384,
    ) -> AsyncIterator[StreamEvent]:
        """Streaming completion. Default falls back to non-streaming."""
        response = await self.create(
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
        )
        yield StreamDone(response=response)

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """Estimate token count for a string."""


def make_tool_use_id() -> str:
    return f"toolu_{uuid.uuid4().hex[:24]}"
