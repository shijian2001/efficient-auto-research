"""Explicit ML-Master benchmark-defined staged FML variant."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

try:
    from mcp.client import streamable_http as _mcp_streamable_http

    if not hasattr(_mcp_streamable_http, "streamablehttp_client"):
        _mcp_streamable_http.streamablehttp_client = (
            _mcp_streamable_http.streamable_http_client
        )
except ImportError:
    pass

from ...contracts import CommandSpec
from ...protocol import write_json_exclusive
from ...registry import ROOT
from .base import FMLAgentAdapter, FMLAgentLaunchContext, native_python_command


class MLMasterAutoresearchVariantFMLAdapter(FMLAgentAdapter):
    agent_id = "ml-master-2"
    native_entrypoint = "evomaster.agent.agent.BaseAgent.run"
    installation_probe_arguments = (
        "-c",
        "from evomaster.agent import Agent, AgentConfig, create_registry",
    )

    def installation_executable(self) -> Path | None:
        return ROOT / "BenchmarkAdapters/environments/agents/ml-master-2-autoresearch/.venv/bin/python"

    def build_native_command(self, context: FMLAgentLaunchContext, prompt: str) -> CommandSpec:
        return native_python_command(
            context=context,
            prompt=prompt,
            python=self.installation_executable(),
            module="BenchmarkAdapters.FMLBench.agents.ml_master_autoresearch_variant",
            source_root=ROOT / "BenchmarkAdapters/environments/agents/ml-master-2-autoresearch/.venv/agent-source",
            label="ML-Master benchmark-defined staged FML variant",
        )


def run_native_loop(
    *, workspace: Path, output_dir: Path, task_input: str, dev_command: str,
    model: str, seed: int, timeout: int, max_steps: int,
) -> dict[str, object]:
    del seed
    from evomaster.agent import Agent, AgentConfig, create_registry
    from evomaster.agent.context import ContextConfig
    from evomaster.agent.session import LocalSession, LocalSessionConfig
    from evomaster.utils import LLMConfig, create_llm
    from evomaster.utils.types import TaskInstance

    class FMLRepositoryAgent(Agent):
        def __init__(self, *args, system_prompt: str, user_prompt: str, **kwargs):
            super().__init__(*args, **kwargs)
            self._fml_system_prompt = system_prompt
            self._fml_user_prompt = user_prompt

        def _get_system_prompt(self) -> str:
            return self._fml_system_prompt

        def _get_user_prompt(self, task: TaskInstance) -> str:
            return self._fml_user_prompt + "\n\n" + task.description

    parameters = json.loads(os.environ["FML_MODEL_PARAMETERS"])
    retry_policy = json.loads(os.environ.get("FML_RETRY_POLICY", "{}"))
    timeout_raw = os.environ.get("FML_REQUEST_TIMEOUT_SECONDS", "")
    llm_options: dict[str, object] = {}
    for name in ("temperature", "reasoning_effort"):
        if parameters.get(name) is not None:
            llm_options[name] = parameters[name]
    output_tokens = parameters.get("max_output_tokens", parameters.get("max_tokens"))
    if output_tokens is not None:
        llm_options["max_tokens"] = int(output_tokens)
    if timeout_raw:
        llm_options["timeout"] = int(timeout_raw)
    retries = retry_policy.get("max_retries")
    if retries is None and retry_policy.get("max_attempts") is not None:
        retries = max(0, int(retry_policy["max_attempts"]) - 1)
    if retries is not None:
        llm_options["max_retries"] = int(retries) + 1
    output_dir.mkdir(parents=True, exist_ok=True)
    session = LocalSession(
        LocalSessionConfig(
            working_dir=str(workspace.resolve()),
            workspace_path=str(workspace.resolve()),
            timeout=timeout,
            config_dir=str(output_dir),
        )
    )
    session.open()
    stages = (
        ("draft", "Inspect the frozen task and implement a strong first candidate."),
        ("research", "Review development evidence and form a distinct generalizable hypothesis."),
        ("improve", "Implement and validate the strongest hypothesis; leave the best revision in place."),
    )
    trajectories = []
    try:
        tools = create_registry(builtin_names=["*"])
        for stage_name, objective in stages:
            llm = create_llm(
                LLMConfig(
                    provider="openai",
                    model=model,
                    api_key=os.environ.get("OPENAI_API_KEY", ""),
                    base_url=os.environ.get("OPENAI_BASE_URL", ""),
                    **llm_options,
                ),
                output_config={"show_in_console": True, "log_to_file": True},
            )
            agent = FMLRepositoryAgent(
                llm=llm,
                session=session,
                tools=tools,
                config=AgentConfig(
                    max_turns=max(1, max_steps // len(stages)),
                    context_config=ContextConfig(
                        max_tokens=256000,
                        truncation_strategy="latest_half",
                        preserve_system_messages=True,
                        preserve_recent_turns=8,
                    ),
                ),
                system_prompt=(
                    "You are ML-Master 2's staged research Agent. Use only native tools, the "
                    "canonical task, and the shared development evaluator. Do not inspect held-out assets."
                ),
                user_prompt=f"{task_input}\nDevelopment evaluator: {dev_command}\nStage: {stage_name}",
                output_config={"show_in_console": True, "log_to_file": True},
                config_dir=output_dir,
                enabled_tool_names=["*"],
            )
            agent.set_agent_name(f"fml_{stage_name}")
            trajectories.append(
                agent.run(
                    TaskInstance(
                        task_id=f"fml-{stage_name}",
                        task_type="repository_optimization",
                        description=objective,
                    )
                )
            )
    finally:
        session.close()
    payload = {
        "native_loop": "evomaster.agent.agent.BaseAgent.run",
        "native_workflow": "draft -> research -> improve",
        "stages": [
            {
                "name": name,
                "status": trajectory.status,
                "steps": len(trajectory.steps),
                "result": trajectory.result,
            }
            for (name, _), trajectory in zip(stages, trajectories)
        ],
    }
    write_json_exclusive(output_dir / "native-result.json", payload)
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
    "MLMasterAutoresearchVariantFMLAdapter",
    "run_native_loop",
]
