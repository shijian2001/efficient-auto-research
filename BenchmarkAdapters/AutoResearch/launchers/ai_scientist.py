"""AiScientist Autoresearch bridge using the native Subagent.run loop."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Callable

from aisci_agent_runtime.shell_interface import ShellInterface
from aisci_agent_runtime.subagents.base import SubagentConfig, SubagentStatus
from aisci_agent_runtime.subagents.terminal_task import TerminalTaskSubagent
from aisci_agent_runtime.tools.base import SubagentCompleteSignal, Tool

from ...autonomous_optimization import task_contract
from ...protocol import write_json_exclusive
from ..dev_client import declare_current, evaluate_current
from .proposal import load_factory


class EvaluateCandidateTool(Tool):
    def __init__(self, workspace: Path, socket_path: str, token: str, state_path: Path) -> None:
        self.workspace = workspace
        self.socket_path = socket_path
        self.token = token
        self.state_path = state_path
        self.contract = task_contract()
        self.experiments_dir = workspace / ".autoresearch-experiments"

    def name(self) -> str:
        return "evaluate_candidate"

    def execute(self, _shell, **_kwargs: object) -> str:
        feedback = evaluate_current(
            self.socket_path,
            self.token,
            self.contract.artifact_path(self.workspace),
            self.state_path,
        )
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        revision_id = str(state["revision_id"])
        self.experiments_dir.mkdir(exist_ok=True)
        shutil.copy2(
            self.contract.artifact_path(self.workspace),
            self.experiments_dir / f"{revision_id}.py",
        )
        (self.experiments_dir / f"{revision_id}.json").write_text(
            json.dumps(feedback, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return json.dumps(feedback, sort_keys=True)

    def get_tool_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": "Create and host-evaluate the current candidate on the development seed.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        }


class ListExperimentsTool(Tool):
    def __init__(self, experiments_dir: Path) -> None:
        self.experiments_dir = experiments_dir

    def name(self) -> str:
        return "list_experiments"

    def execute(self, _shell, **_kwargs: object) -> str:
        records = []
        for path in sorted(self.experiments_dir.glob("candidate-*.json")):
            records.append(json.loads(path.read_text(encoding="utf-8")))
        return json.dumps(records, sort_keys=True)

    def get_tool_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": "List all development-evaluated candidate records from this run.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        }


class RestoreCandidateTool(Tool):
    def __init__(self, workspace: Path, experiments_dir: Path, state_path: Path) -> None:
        self.workspace = workspace
        self.experiments_dir = experiments_dir
        self.state_path = state_path
        self.contract = task_contract()

    def name(self) -> str:
        return "restore_candidate"

    def execute(self, _shell, revision_id: str, **_kwargs: object) -> str:
        if not revision_id.startswith("candidate-") or not revision_id.removeprefix("candidate-").isdigit():
            raise RuntimeError("invalid candidate revision ID")
        source = self.experiments_dir / f"{revision_id}.py"
        feedback_path = self.experiments_dir / f"{revision_id}.json"
        if not source.is_file() or source.is_symlink():
            raise RuntimeError(f"unknown evaluated candidate: {revision_id}")
        if not feedback_path.is_file() or feedback_path.is_symlink():
            raise RuntimeError(f"candidate feedback is unavailable: {revision_id}")
        feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
        shutil.copy2(source, self.contract.artifact_path(self.workspace))
        self.state_path.write_text(
            json.dumps(
                {
                    "revision_id": revision_id,
                    "candidate_sha256": feedback["candidate_sha256"],
                    "last_feedback": feedback,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return json.dumps({"restored_revision_id": revision_id}, sort_keys=True)

    def get_tool_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": "Restore train.py from a previously evaluated candidate revision.",
                "parameters": {
                    "type": "object",
                    "properties": {"revision_id": {"type": "string"}},
                    "required": ["revision_id"],
                    "additionalProperties": False,
                },
            },
        }


class CompleteCurrentTool(Tool):
    def __init__(
        self,
        socket_path: str,
        token: str,
        state_path: Path,
    ) -> None:
        self.socket_path = socket_path
        self.token = token
        self.state_path = state_path

    def name(self) -> str:
        return "complete_current"

    def execute(self, _shell, **_kwargs: object) -> str:
        declared = declare_current(self.socket_path, self.token, self.state_path)
        raise SubagentCompleteSignal(
            content=f"Declared Agent-selected development candidate {declared['revision_id']}",
            artifacts={"declared_revision_id": declared["revision_id"]},
        )

    def get_tool_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": "Declare the currently selected evaluated candidate, then finish.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        }


class ArchitectureDesignSubagent(TerminalTaskSubagent):
    """Autoresearch domain identity over AiScientist's native typed-tool step loop."""

    def __init__(
        self,
        *args: object,
        workspace: Path,
        socket_path: str,
        token: str,
        state_path: Path,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.workspace = workspace
        self.socket_path = socket_path
        self.token = token
        self.state_path = state_path

    def system_prompt(self) -> str:
        contract = task_contract()
        return (
            f"You are AiScientist's {contract.task_name} subagent. {contract.task_instruction} "
            "Use evaluate_candidate for trusted development feedback, list_experiments and "
            "restore_candidate to compare and explicitly select revisions, then complete_current to finish. Never infer or seek "
            "held-out seeds and never edit benchmark, evaluator, protocol, or capability files."
        )

    def get_tools(self) -> list[Tool]:
        tools = [tool for tool in super().get_tools() if tool.name() != "subagent_complete"]
        evaluator = EvaluateCandidateTool(
            self.workspace,
            self.socket_path,
            self.token,
            self.state_path,
        )
        return [
            *tools,
            evaluator,
            ListExperimentsTool(evaluator.experiments_dir),
            RestoreCandidateTool(
                self.workspace,
                evaluator.experiments_dir,
                self.state_path,
            ),
            CompleteCurrentTool(
                self.socket_path,
                self.token,
                self.state_path,
            ),
        ]


def run_native_loop(
    *,
    workspace: Path,
    output_dir: Path,
    socket_path: str,
    token: str,
    seed: int,
    timeout: int,
    max_steps: int | None = None,
    llm_factory: Callable[..., object] | None = None,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    contract = task_contract()
    llm_factory = llm_factory or load_factory("AUTORESEARCH_AISCIENTIST_LLM_FACTORY")
    artifact_path = contract.artifact_path(workspace)
    state_path = contract.state_path(workspace)
    evaluate_command = (
        f"python -m BenchmarkAdapters.AutoResearch.dev_client evaluate-current --socket "
        f"{socket_path} --token {token} --train {artifact_path} --state {state_path}"
    )
    declare_command = (
        f"python -m BenchmarkAdapters.AutoResearch.dev_client declare-current --socket "
        f"{socket_path} --token {token} --state {state_path}"
    )
    subagent = ArchitectureDesignSubagent(
        shell=ShellInterface(working_dir=str(workspace.resolve())),
        llm=llm_factory(seed=seed),
        config=SubagentConfig(
            max_steps=max_steps or 500,
            time_limit=timeout,
            reminder_freq=20,
            log_dir=str(output_dir / "logs"),
            output_dir=str(output_dir),
        ),
        workspace=workspace,
        socket_path=socket_path,
        token=token,
        state_path=state_path,
    )
    result = subagent.run(
        f"Optimize the frozen task using the native typed capabilities. The metric is "
        f"{contract.metric_name} and {contract.metric_direction} is better. Evaluate serious candidates "
        "then explicitly select a revision with restore_candidate and finish with complete_current."
    )
    if not state_path.is_file():
        evaluate_current(socket_path, token, artifact_path, state_path)
    declared = declare_current(socket_path, token, state_path)
    payload = {
        "native_component": contract.native_component or "native-ai-scientist-subagent",
        "native_loop": "aisci_agent_runtime.subagents.base.Subagent.run",
        "status": result.status.value,
        "steps": result.num_steps,
        "runtime_seconds": result.runtime_seconds,
        "token_usage": dict(result.token_usage),
        "declared_revision_id": declared["revision_id"],
    }
    write_json_exclusive(output_dir / "native-result.json", payload)
    if result.status not in {SubagentStatus.COMPLETED, SubagentStatus.TIMEOUT}:
        raise RuntimeError(f"AiScientist native Subagent ended with {result.status.value}")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--timeout", type=int, required=True)
    parser.add_argument("--max-steps", type=int)
    args = parser.parse_args(argv)
    run_native_loop(
        workspace=args.workspace,
        output_dir=args.output_dir,
        socket_path=args.socket,
        token=args.token,
        seed=args.seed,
        timeout=args.timeout,
        max_steps=args.max_steps,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ArchitectureDesignSubagent",
    "CompleteCurrentTool",
    "EvaluateCandidateTool",
    "ListExperimentsTool",
    "RestoreCandidateTool",
    "run_native_loop",
]
