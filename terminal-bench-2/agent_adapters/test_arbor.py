from __future__ import annotations

import asyncio
from pathlib import Path

from harbor.models.agent.context import AgentContext

from arbor.core.llm.base import LLMResponse, TextBlock, ToolUseBlock, Usage
from agent_adapters import arbor as arbor_adapter


class _Result:
    def __init__(self, stdout: str = "", stderr: str = "", return_code: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.return_code = return_code


class _Environment:
    def __init__(self, root: Path):
        self.root = root

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        del cwd, env, timeout_sec, user
        if command == "pwd":
            return _Result(stdout="/app\n")
        return _Result(stdout="ok")

    async def upload_file(self, source_path, target_path):
        target = self.root / target_path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(source_path).read_bytes())

    async def download_file(self, source_path, target_path):
        source = self.root / source_path.lstrip("/")
        Path(target_path).write_bytes(source.read_bytes())


class _ScriptedProvider:
    model = "scripted"

    def __init__(self):
        self.calls = 0

    async def create(self, **kwargs):
        del kwargs
        self.calls += 1
        if self.calls == 1:
            block = ToolUseBlock(
                id="write-1",
                name="Write",
                input={"file_path": "answer.txt", "content": "native-arbor"},
            )
            return LLMResponse(
                content=[block],
                stop_reason="tool_use",
                usage=Usage(input_tokens=3, output_tokens=2),
                model=self.model,
                raw_content=[
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }
                ],
            )
        block = TextBlock("task complete")
        return LLMResponse(
            content=[block],
            stop_reason="end_turn",
            usage=Usage(input_tokens=4, output_tokens=2),
            model=self.model,
            raw_content=[{"type": "text", "text": block.text}],
        )

    def count_tokens(self, text: str) -> int:
        return len(text.split())


def test_arbor_native_loop_writes_through_harbor(tmp_path: Path, monkeypatch) -> None:
    provider = _ScriptedProvider()
    monkeypatch.setattr(arbor_adapter, "create_provider", lambda config: provider)
    agent = arbor_adapter.ArborTerminalAgent(
        logs_dir=tmp_path / "logs",
        model_name="gpt-5.5",
    )
    environment = _Environment(tmp_path / "container")

    async def scenario() -> AgentContext:
        context = AgentContext()
        await agent.setup(environment)
        await agent.run("write the answer", environment, context)
        return context

    context = asyncio.run(scenario())
    assert (environment.root / "app/answer.txt").read_text() == "native-arbor"
    assert context.metadata["native_loop"] == "arbor.core.agent.Agent.run"
    assert context.metadata["stop_reason"] == "finished"
    assert context.n_input_tokens == 7
    assert context.n_output_tokens == 4


def test_arbor_non_finished_stop_still_returns_for_verifier(tmp_path: Path, monkeypatch) -> None:
    provider = _ScriptedProvider()
    monkeypatch.setattr(arbor_adapter, "create_provider", lambda config: provider)
    monkeypatch.setenv("ARBOR_TERMINAL_MAX_TURNS", "1")
    agent = arbor_adapter.ArborTerminalAgent(logs_dir=tmp_path / "logs")
    environment = _Environment(tmp_path / "container")

    async def scenario() -> AgentContext:
        context = AgentContext()
        await agent.setup(environment)
        await agent.run("write the answer", environment, context)
        return context

    context = asyncio.run(scenario())
    assert context.metadata["stop_reason"] == "max_turns"
