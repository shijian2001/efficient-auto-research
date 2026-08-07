from __future__ import annotations

import asyncio
from pathlib import Path

from harbor.agents.factory import AgentFactory
from harbor.models.agent.context import AgentContext

from aisci_agent_runtime.llm_client import LLMResponse, ToolCallResult
from agent_adapters.ai_scientist import AiScientistTerminalAgent


class FakeEnvironment:
    pass


class FakeBridge:
    def __init__(self, environment=None, cancel_event=None):
        self.environment = environment
        self.cancel_event = cancel_event


class ScriptedLLM:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools=None):
        del messages, tools
        self.calls += 1
        if self.calls != 1:
            raise AssertionError("scripted completion should stop the native loop")
        return LLMResponse(
            text_content=None,
            tool_calls=[
                ToolCallResult(
                    call_id="done",
                    name="subagent_complete",
                    arguments={"content": "fake Harbor task complete"},
                )
            ],
            usage={"input": 1, "output": 1},
            raw_message=None,
        )


def test_agent_factory_loads_and_runs_with_fake_harbor(tmp_path: Path) -> None:
    agent = AgentFactory.create_agent_from_import_path(
        "agent_adapters.ai_scientist:AiScientistTerminalAgent",
        logs_dir=tmp_path / "logs",
        model_name="openai/test-model",
        bridge_factory=FakeBridge,
        llm_factory=ScriptedLLM,
        cancellation_runner=lambda callback, *args, **kwargs: callback(*args),
    )
    assert isinstance(agent, AiScientistTerminalAgent)
    assert agent.SUPPORTS_ATIF is False

    environment = FakeEnvironment()
    async def execute() -> AgentContext:
        await agent.setup(environment)
        context = AgentContext()
        await agent.run("finish immediately", environment, context)
        return context

    context = asyncio.run(execute())

    assert context.metadata["implementation"] == "native-ai-scientist-subagent"
    assert context.metadata["native_loop"].endswith("Subagent.run")
    assert "subagent_complete" in context.metadata["native_tools"]
    assert context.metadata["status"] == "completed"
