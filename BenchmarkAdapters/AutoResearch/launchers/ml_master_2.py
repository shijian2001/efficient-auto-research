"""ML-Master 2 Autoresearch bridge using EvoMaster's native staged Agent.run workflow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

try:
    from mcp.client import streamable_http as _mcp_streamable_http

    if not hasattr(_mcp_streamable_http, "streamablehttp_client"):
        _mcp_streamable_http.streamablehttp_client = _mcp_streamable_http.streamable_http_client
except ImportError:
    pass

from evomaster.agent import Agent, AgentConfig, create_registry
from evomaster.agent.context import ContextConfig
from evomaster.agent.session import LocalSession, LocalSessionConfig
from evomaster.utils.types import TaskInstance

from ...protocol import write_json_exclusive

from ...autonomous_optimization import task_contract
from ..dev_client import declare_current, evaluate_current
from .proposal import load_factory


class ArchitectureDesignAgent(Agent):
    def __init__(self, *args, system_prompt: str, user_prompt: str, **kwargs):
        super().__init__(*args, **kwargs)
        self._system_prompt = system_prompt
        self._user_prompt = user_prompt

    def _get_system_prompt(self) -> str:
        return self._system_prompt

    def _get_user_prompt(self, task: TaskInstance) -> str:
        return self._user_prompt + "\n\n" + task.description


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
    llm_factory = llm_factory or load_factory("AUTORESEARCH_EVOMASTER_LLM_FACTORY")
    artifact_path = contract.artifact_path(workspace)
    state_path = contract.state_path(workspace)
    evaluate_command = (
        f"python -m BenchmarkAdapters.AutoResearch.dev_client evaluate-current --socket "
        f"{socket_path} --token {token} --train {artifact_path} --state {state_path}"
    )
    session = LocalSession(
        LocalSessionConfig(
            working_dir=str(workspace.resolve()),
            workspace_path=str(workspace.resolve()),
            timeout=timeout,
            config_dir=str(output_dir),
        )
    )
    session.open()
    stage_records: list[dict[str, object]] = []
    remaining_steps = max_steps
    try:
        tools = create_registry(builtin_names=["*"])
        for stage_name, objective in (
            ("draft", "Develop and evaluate a strong initial architecture/training recipe."),
            ("research", "Analyze structured dev feedback and form a distinct research direction."),
            ("improve", "Implement and evaluate the strongest generalizable improvement."),
            ("review", "Review all dev evidence and restore the strongest observed train.py."),
        ):
            if remaining_steps is not None and remaining_steps <= 0:
                break
            agent = ArchitectureDesignAgent(
                llm=llm_factory(stage=stage_name, seed=seed),
                session=session,
                tools=tools,
                config=AgentConfig(
                    max_turns=remaining_steps if remaining_steps is not None else 100,
                    context_config=ContextConfig(
                        max_tokens=256000,
                        truncation_strategy="latest_half",
                        preserve_system_messages=True,
                        preserve_recent_turns=8,
                    ),
                ),
                system_prompt=(
                    f"You are ML-Master 2's staged research Agent for {contract.task_name}. "
                    f"{contract.task_instruction} Optimize {contract.metric_name} by "
                    f"{contract.metric_direction}, and never access held-out evaluation."
                ),
                user_prompt=f"Stage: {stage_name}. {objective}\nDev evaluator: {evaluate_command}",
                output_config={"show_in_console": True, "log_to_file": True},
                config_dir=output_dir,
                enabled_tool_names=["*"],
            )
            agent.set_agent_name(f"autoresearch_{stage_name}")
            trajectory = agent.run(
                TaskInstance(
                    task_id=f"autoresearch-{stage_name}",
                    task_type="architecture_design",
                    description=objective,
                )
            )
            steps_used = len(trajectory.steps)
            if remaining_steps is not None:
                remaining_steps -= max(1, steps_used)
            feedback = evaluate_current(socket_path, token, artifact_path, state_path)
            stage_records.append(
                {
                    "stage": stage_name,
                    "trajectory_status": trajectory.status,
                    "steps": steps_used,
                    "dev_feedback": feedback,
                }
            )
    finally:
        session.close()
    declared = declare_current(socket_path, token, state_path)
    payload = {
        "native_component": contract.native_component or "native-ml-master-2-workflow",
        "native_loop": "evomaster.agent.agent.BaseAgent.run",
        "native_workflow": "draft -> research -> improve -> review",
        "declared_revision_id": declared["revision_id"],
        "stages": stage_records,
    }
    write_json_exclusive(output_dir / "native-result.json", payload)
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


__all__ = ["ArchitectureDesignAgent", "run_native_loop"]
