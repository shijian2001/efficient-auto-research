from __future__ import annotations

from pathlib import Path

from aisci_agent_runtime.llm_client import (
    ContextLengthError,
    LLMCancelledError,
    LLMResponse,
    ToolCallResult,
)
from aisci_agent_runtime.shell_interface import HarborShellInterface
from aisci_agent_runtime.subagents.base import SubagentConfig, SubagentStatus
from aisci_agent_runtime.subagents.terminal_task import TerminalTaskSubagent


class FakeHarborBridge:
    def __init__(self, root: Path):
        self.root = root
        self.commands: list[dict[str, object]] = []

    def run(self, command: str, cwd: str, timeout: int):
        import subprocess

        self.commands.append({"command": command, "cwd": cwd, "timeout": timeout})
        result = subprocess.run(
            ["bash", "-lc", command],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout, result.stderr, result.returncode

    def write_file(self, path: str, content: str) -> None:
        target = self.root / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def read_file(self, path: str) -> str:
        return (self.root / path.lstrip("/")).read_text(encoding="utf-8")

    def file_exists(self, path: str) -> bool:
        return (self.root / path.lstrip("/")).exists()


class ScriptedLLM:
    def __init__(self):
        self.calls = 0
        self.schemas: list[list[dict]] = []

    def chat(self, messages: list[dict], tools: list[dict] | None = None):
        del messages
        self.calls += 1
        self.schemas.append(tools or [])
        if self.calls == 1:
            return LLMResponse(
                text_content="running a shell check",
                tool_calls=[
                    ToolCallResult(
                        call_id="bash-1",
                        name="bash",
                        arguments={"command": "printf native-loop"},
                    )
                ],
                usage={"input": 3, "output": 2},
                raw_message=None,
            )
        return LLMResponse(
            text_content=None,
            tool_calls=[
                ToolCallResult(
                    call_id="complete-1",
                    name="subagent_complete",
                    arguments={"content": "completed through the native loop"},
                )
            ],
            usage={"input": 4, "output": 2},
            raw_message=None,
        )


def test_harbor_shell_interface_preserves_sync_protocol(tmp_path: Path) -> None:
    bridge = FakeHarborBridge(tmp_path)
    shell = HarborShellInterface(bridge, working_dir="/workspace")

    result = shell.send_shell_command("printf hello")

    assert result.output == "hello"
    assert result.exit_code == 0
    assert bridge.commands[0]["cwd"] == "/workspace"


def test_terminal_task_subagent_uses_native_loop_and_schemas(tmp_path: Path) -> None:
    bridge = FakeHarborBridge(tmp_path)
    llm = ScriptedLLM()
    subagent = TerminalTaskSubagent(
        shell=HarborShellInterface(bridge),
        llm=llm,
        config=SubagentConfig(
            max_steps=3,
            time_limit=10,
            reminder_freq=10,
            log_dir=str(tmp_path / "logs"),
        ),
    )

    output = subagent.run("print a marker")

    assert output.status is SubagentStatus.COMPLETED
    assert output.content == "completed through the native loop"
    assert output.num_steps == 2
    assert [schema["function"]["name"] for schema in llm.schemas[0]] == [
        "bash",
        "python",
        "read_file_chunk",
        "search_file",
        "edit_file",
        "subagent_complete",
    ]
    assert bridge.commands[0]["command"] == "printf native-loop"


def test_terminal_task_maps_llm_cancellation_to_native_status(tmp_path: Path) -> None:
    class CancelledLLM:
        def chat(self, messages, tools=None):
            del messages, tools
            raise LLMCancelledError("cancelled")

    subagent = TerminalTaskSubagent(
        shell=HarborShellInterface(FakeHarborBridge(tmp_path)),
        llm=CancelledLLM(),
        config=SubagentConfig(max_steps=1, time_limit=10, log_dir=str(tmp_path / "logs")),
    )
    output = subagent.run("cancel")
    assert output.status is SubagentStatus.CANCELLED


def test_terminal_task_maps_post_pruning_failure_to_failed_status(tmp_path: Path) -> None:
    class ContextThenFailureLLM:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, tools=None):
            del messages, tools
            self.calls += 1
            if self.calls == 1:
                raise ContextLengthError("too long")
            raise RuntimeError("retry failed")

    subagent = TerminalTaskSubagent(
        shell=HarborShellInterface(FakeHarborBridge(tmp_path)),
        llm=ContextThenFailureLLM(),
        config=SubagentConfig(max_steps=1, time_limit=10, log_dir=str(tmp_path / "logs")),
    )
    output = subagent.run("retry")
    assert output.status is SubagentStatus.FAILED
    assert "retry after context pruning failed" in output.content
