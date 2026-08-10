"""Explicit AiScientist TerminalTaskSubagent FML patched variant."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from ...contracts import CommandSpec
from ...protocol import write_json_exclusive
from ...registry import ROOT
from .base import FMLAgentAdapter, FMLAgentLaunchContext, native_python_command


class AiScientistTerminalVariantFMLAdapter(FMLAgentAdapter):
    agent_id = "ai-scientist"
    native_entrypoint = "aisci_agent_runtime.subagents.terminal_task.TerminalTaskSubagent.run"
    installation_probe_arguments = (
        "-c",
        "from aisci_agent_runtime.subagents.terminal_task import TerminalTaskSubagent",
    )

    def installation_executable(self) -> Path | None:
        return ROOT / "BenchmarkAdapters/environments/terminal/ai-scientist/.venv/bin/python"

    def build_native_command(self, context: FMLAgentLaunchContext, prompt: str) -> CommandSpec:
        return native_python_command(
            context=context,
            prompt=prompt,
            python=self.installation_executable(),
            module="BenchmarkAdapters.FMLBench.agents.ai_scientist_terminal_variant",
            source_root=ROOT / "BenchmarkAdapters/environments/terminal/ai-scientist/.venv/agent-source/src",
            label="AiScientist TerminalTaskSubagent FML variant",
        )


def run_native_loop(
    *, workspace: Path, output_dir: Path, task_input: str, dev_command: str,
    model: str, seed: int, timeout: int, max_steps: int,
) -> dict[str, object]:
    del seed
    from aisci_agent_runtime.llm_client import LLMConfig, create_llm_client
    from aisci_agent_runtime.shell_interface import ShellInterface
    from aisci_agent_runtime.subagents.base import SubagentConfig, SubagentStatus
    from aisci_agent_runtime.subagents.terminal_task import TerminalTaskSubagent

    parameters = json.loads(os.environ["FML_MODEL_PARAMETERS"])
    llm_options: dict[str, object] = {}
    output_tokens = parameters.get("max_output_tokens", parameters.get("max_tokens"))
    if output_tokens is not None:
        llm_options["max_tokens"] = int(output_tokens)
    for name in ("temperature", "reasoning_effort"):
        if parameters.get(name) is not None:
            llm_options[name] = parameters[name]
    timeout_raw = os.environ.get("FML_REQUEST_TIMEOUT_SECONDS", "")
    if timeout_raw:
        llm_options["request_timeout"] = float(timeout_raw)
    output_dir.mkdir(parents=True, exist_ok=True)
    llm = create_llm_client(
        LLMConfig(
            provider="openai",
            model=model,
            api_key=os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get("OPENAI_BASE_URL"),
            api_mode="completions",
            **llm_options,
        )
    )
    subagent = TerminalTaskSubagent(
        shell=ShellInterface(working_dir=str(workspace.resolve())),
        llm=llm,
        config=SubagentConfig(
            max_steps=max_steps,
            time_limit=timeout,
            reminder_freq=max(1, min(20, max_steps)),
            log_dir=str(output_dir / "logs"),
            output_dir=str(output_dir),
        ),
    )
    result = subagent.run(
        task_input
        + "\nUse the shared development evaluator only through this command: "
        + dev_command
        + "\nLeave the best valid revision in place before completing."
    )
    payload = {
        "native_loop": "aisci_agent_runtime.subagents.terminal_task.TerminalTaskSubagent.run",
        "agent_system": "AweAI AiScientist (not FML upstream The AI Scientist v1/v2)",
        "status": result.status.value,
        "content": result.content,
        "num_steps": result.num_steps,
        "runtime_seconds": result.runtime_seconds,
        "token_usage": result.token_usage,
        "log_path": result.log_path,
    }
    write_json_exclusive(output_dir / "native-result.json", payload)
    if result.status not in {SubagentStatus.COMPLETED, SubagentStatus.TIMEOUT}:
        raise RuntimeError(f"AiScientist native FML loop ended with {result.status.value}")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-run", action="store_true")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--task-input", required=True)
    parser.add_argument("--dev-command", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--timeout", type=int, required=True)
    parser.add_argument("--max-steps", type=int, required=True)
    args = parser.parse_args(argv)
    run_native_loop(**{name: value for name, value in vars(args).items() if name != "native_run"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AiScientistTerminalVariantFMLAdapter",
    "run_native_loop",
]
