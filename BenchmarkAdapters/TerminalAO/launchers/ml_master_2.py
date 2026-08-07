"""ML-Master 2 repository-domain launcher using EvoMaster's native Agent loop."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from evomaster.agent import Agent, AgentConfig, create_registry
from evomaster.agent.context import ContextConfig
from evomaster.agent.session import LocalSession, LocalSessionConfig
from evomaster.utils import LLMConfig, create_llm
from evomaster.utils.types import TaskInstance


SYSTEM_PROMPT = """You are ML-Master 2's repository optimization agent.
Operate only in the configured terminus-2 repository workspace. Inspect the harness,
form hypotheses, edit only the allowlisted files from the task, and evaluate each serious
candidate using the DEV-only evaluation command. Hidden test information is unavailable.
Use the native shell/editor tools and call finish only after leaving the best harness in place.
"""


class RepositoryAgent(Agent):
    def __init__(self, *args, system_prompt: str, user_prompt: str, **kwargs):
        super().__init__(*args, **kwargs)
        self._repository_system_prompt = system_prompt
        self._repository_user_prompt = user_prompt

    def _get_system_prompt(self) -> str:
        return self._repository_system_prompt

    def _get_user_prompt(self, task: TaskInstance) -> str:
        return self._repository_user_prompt + "\n\n" + task.description


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
        raise RuntimeError(f"refusing to overwrite ML-Master 2 result: {output_dir}")
    session = LocalSession(
        LocalSessionConfig(
            working_dir=str(workspace.resolve()),
            workspace_path=str(workspace.resolve()),
            timeout=timeout,
            config_dir=str(output_dir),
        )
    )
    session.open()
    try:
        tools = create_registry(builtin_names=["*"])
        user_prompt = (
            "Optimize this frozen terminus-2 harness for held-out Terminal-Bench pass rate. "
            "Only edit terminus_2.py, terminus_json_plain_parser.py, "
            "terminus_xml_plain_parser.py, tmux_session.py, and templates/. "
            f"Obtain structured aggregate DEV feedback with: {dev_command}. "
            "Do not infer or access the held-out test split."
        )
        stage_specs = (
            (
                "draft",
                "Inspect the frozen baseline, run the DEV evaluator, form multiple hypotheses, "
                "implement the strongest initial candidate, and leave it in place.",
            ),
            (
                "research",
                "Review the current diff and DEV evidence. Diagnose broad failure modes and develop "
                "a new generalizable direction rather than overfitting individual tasks.",
            ),
            (
                "improve",
                "Implement and validate the strongest research direction. Compare structured DEV "
                "feedback and leave the best observed harness in place.",
            ),
        )
        trajectories = []
        for stage_name, stage_instruction in stage_specs:
            stage_llm = create_llm(
                LLMConfig(
                    provider="openai",
                    model=model,
                    api_key=os.environ.get("OPENAI_API_KEY", ""),
                    base_url=os.environ.get("OPENAI_BASE_URL", ""),
                    temperature=1.0,
                    max_tokens=65536,
                    timeout=3600,
                    max_retries=3,
                )
            )
            agent = RepositoryAgent(
                llm=stage_llm,
                session=session,
                tools=tools,
                config=AgentConfig(
                    max_turns=100,
                    context_config=ContextConfig(
                        max_tokens=256000,
                        truncation_strategy="latest_half",
                        preserve_system_messages=True,
                        preserve_recent_turns=8,
                    ),
                ),
                system_prompt=f"{SYSTEM_PROMPT}\nCurrent ML-Master 2 stage: {stage_name}.",
                user_prompt=f"{user_prompt}\n\nStage objective: {stage_instruction}",
                output_config={"show_in_console": True, "log_to_file": True},
                config_dir=output_dir,
                enabled_tool_names=["*"],
            )
            agent.set_agent_name(f"terminal_ao_{stage_name}")
            trajectory = agent.run(
                TaskInstance(
                    task_id=f"terminal-ao-{stage_name}",
                    task_type="repository_optimization",
                    description=stage_instruction,
                )
            )
            trajectories.append(trajectory)
    finally:
        session.close()
    payload = {
        "native_loop": "evomaster.agent.agent.BaseAgent.run",
        "native_workflow": "ml-master-2 draft -> research -> improve",
        "status": trajectories[-1].status,
        "stages": [
            {
                "name": name,
                "status": trajectory.status,
                "steps": len(trajectory.steps),
                "result": trajectory.result,
            }
            for (name, _), trajectory in zip(stage_specs, trajectories)
        ],
    }
    (output_dir / "native-result.json").write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    if trajectories[-1].status not in {"completed", "failed"}:
        raise RuntimeError(f"ML-Master 2 native loop ended with {trajectories[-1].status}")
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


__all__ = ["RepositoryAgent", "run_native_loop"]
