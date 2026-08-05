"""Executor tool — spawn a child agent for parallel exploration.
Description ported from Claude Code's AgentTool."""

# pylint: disable=broad-exception-caught

from __future__ import annotations

import asyncio
import logging
from typing import Any, TYPE_CHECKING

from .base import Tool

if TYPE_CHECKING:
    from ..agent import Agent

log = logging.getLogger(__name__)


class ExecutorTool(Tool):
    """Spawn a child agent to work on a sub-task independently.

    The child shares tools and system prompt but has a separate conversation.
    """

    name = "Executor"
    description = (
        "Launch a new agent to handle complex, multi-step tasks.\n"
        "\n"
        "When using this tool:\n"
        "- Always include a short description summarizing what the agent "
        "will do\n"
        "- When the agent is done, it will return a single message back to "
        "you. The result is not visible to the user. To show the user the "
        "result, send a text message with a concise summary.\n"
        "- Clearly tell the agent whether you expect it to write code or "
        "just to do research (search, file reads, etc.), since it is not "
        "aware of the user's intent\n"
        "- Brief the agent like a smart colleague who just walked into the "
        "room — it hasn't seen this conversation, doesn't know what you've "
        "tried, doesn't understand why the task matters.\n"
        "- Explain what you're trying to accomplish and why.\n"
        "- Describe what you've already learned or ruled out.\n"
        "- Give enough context that the agent can make judgment calls.\n"
        "\n"
        "Use cases:\n"
        "- Exploring alternative optimization strategies in parallel\n"
        "- Researching code while the main agent continues other work\n"
        "- Running independent experiments on different approaches\n"
        "\n"
        "Terse command-style prompts produce shallow, generic work. "
        "Provide detailed context for quality results."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Short description of what the executor should do (3-5 words).",
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Detailed instructions for the executor. Be specific — "
                    "the executor has no context from the parent conversation. "
                    "Include file paths, what to change, and why."
                ),
            },
            "max_turns": {
                "type": "integer",
                "description": "Maximum turns for the executor (default: 30).",
            },
        },
        "required": ["description", "prompt"],
    }
    is_read_only = False  # Sub-agents can make changes
    max_result_chars = 50_000

    def __init__(self, *, cwd: str, parent_agent: Agent, **kwargs: Any):
        super().__init__(cwd=cwd, **kwargs)
        self._parent = parent_agent

    async def execute(self, **kwargs: Any) -> str:
        from ..agent import Agent
        from ..config import AgentConfig

        description: str = kwargs["description"]
        prompt: str = kwargs["prompt"]
        max_turns: int = kwargs.get("max_turns", 30)

        log.info("Spawning executor: %s", description)

        # Create a child config: share the parent's llm/timeout/context
        # subgroups wholesale, override only the per-spawn fields.
        parent = self._parent.config
        child_config = AgentConfig(
            llm=parent.llm.model_copy(deep=True),
            timeout=parent.timeout.model_copy(deep=True),
            context=parent.context.model_copy(deep=True),
            cwd=parent.cwd,
            max_turns=max_turns,
            auto_git=False,  # Don't auto-commit from executors
            verbose=parent.verbose,
        )
        child_config.run_training_stage_timeouts = parent.run_training_stage_timeouts
        child_config.budget_policy_summary = parent.budget_policy_summary

        # Create child agent — exclude Executor to prevent recursion bombs
        child_tools = [t for t in self._parent.tools.values() if t.name != "Executor"]
        child_agent = Agent(
            provider=self._parent.provider,
            tools=child_tools,
            system_prompt=self._parent.system_prompt,
            config=child_config,
        )

        try:
            result = await asyncio.wait_for(
                child_agent.run(prompt),
                timeout=self._parent.config.nested_executor_timeout,
            )
        except asyncio.TimeoutError:
            result = f"[Sub-agent timed out after {self._parent.config.nested_executor_timeout}s]"
        except Exception as e:
            result = f"[Sub-agent error: {e}]"

        log.info(
            "Sub-agent '%s' finished: %d turns, %d/%d tokens",
            description,
            child_agent.total_turns,
            child_agent.total_input_tokens,
            child_agent.total_output_tokens,
        )

        return self._truncate(result)
