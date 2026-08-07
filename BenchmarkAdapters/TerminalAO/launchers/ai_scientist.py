"""AiScientist native terminal-subagent launcher for Terminal AO."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from aisci_agent_runtime.llm_client import LLMConfig, create_llm_client
from aisci_agent_runtime.shell_interface import ShellInterface
from aisci_agent_runtime.subagents.base import SubagentConfig, SubagentStatus
from aisci_agent_runtime.subagents.terminal_task import TerminalTaskSubagent


def run_native_loop(
    *,
    workspace: Path,
    output_dir: Path,
    dev_command: str,
    model: str,
    timeout: int,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if (output_dir / "native-result.json").exists():
        raise RuntimeError(f"refusing to overwrite AiScientist result: {output_dir}")
    llm = create_llm_client(
        LLMConfig(
            provider="openai",
            model=model,
            max_tokens=65536,
            api_key=os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get("OPENAI_BASE_URL"),
            temperature=1.0,
            api_mode="completions",
            reasoning_effort="high",
            context_window=256000,
        )
    )
    subagent = TerminalTaskSubagent(
        shell=ShellInterface(working_dir=str(workspace.resolve())),
        llm=llm,
        config=SubagentConfig(
            max_steps=500,
            time_limit=timeout,
            reminder_freq=20,
            log_dir=str(output_dir / "logs"),
            output_dir=str(output_dir),
        ),
    )
    result = subagent.run(
        "Optimize the terminus-2 harness in the current repository. Only modify the allowed "
        "harness paths described by the task. Evaluate candidate revisions with this DEV-only "
        f"command: {dev_command}. Use its structured aggregate feedback to improve the harness. "
        "Never seek hidden test identities or rewards. Finish with the best candidate left in place."
    )
    payload = {
        "native_loop": "aisci_agent_runtime.subagents.terminal_task.TerminalTaskSubagent.run",
        "status": result.status.value,
        "content": result.content,
        "num_steps": result.num_steps,
        "runtime_seconds": result.runtime_seconds,
        "token_usage": result.token_usage,
        "log_path": result.log_path,
    }
    (output_dir / "native-result.json").write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    if result.status not in {SubagentStatus.COMPLETED, SubagentStatus.TIMEOUT}:
        raise RuntimeError(f"AiScientist native loop ended with {result.status.value}")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dev-command", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--timeout", type=int, required=True)
    args = parser.parse_args(argv)
    os.environ["PYTHONHASHSEED"] = str(args.seed)
    run_native_loop(
        workspace=args.workspace,
        output_dir=args.output_dir,
        dev_command=args.dev_command,
        model=args.model,
        timeout=args.timeout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["run_native_loop"]
