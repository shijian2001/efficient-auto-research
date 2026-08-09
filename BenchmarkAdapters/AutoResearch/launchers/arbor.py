"""Arbor Autoresearch bridge using the native coordinator ReAct loop."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
from pathlib import Path
from typing import Callable

from arbor.coordinator.config import CoordinatorConfig
from arbor.coordinator.orchestrator import CoordinatorOrchestrator
from arbor.core.llm.base import LLMProvider

from ...autonomous_optimization import task_contract
from ...protocol import write_json_exclusive
from ..dev_client import declare_current, evaluate_current


def _load_provider_factory() -> Callable[..., LLMProvider]:
    value = os.environ.get("AUTORESEARCH_ARBOR_PROVIDER_FACTORY")
    if not value or ":" not in value:
        raise RuntimeError(
            "AUTORESEARCH_ARBOR_PROVIDER_FACTORY=module:callable is required; "
            "model-provider wiring is a separate Adapter"
        )
    module_name, attribute = value.split(":", 1)
    factory = getattr(importlib.import_module(module_name), attribute)
    if not callable(factory):
        raise RuntimeError(f"configured Arbor provider factory is not callable: {value}")
    return factory


def run_native_loop(
    *,
    workspace: Path,
    output_dir: Path,
    socket_path: str,
    token: str,
    seed: int,
    timeout: int,
    max_turns: int = 96,
    provider_factory: Callable[..., LLMProvider] | None = None,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    contract = task_contract()
    artifact_path = contract.artifact_path(workspace)
    state_path = contract.state_path(workspace)
    evaluate_command = (
        "python -m BenchmarkAdapters.AutoResearch.dev_client evaluate-current "
        f"--socket {socket_path} --token {token} --train {artifact_path} "
        f"--state {state_path}"
    )
    declare_command = (
        "python -m BenchmarkAdapters.AutoResearch.dev_client declare-current "
        f"--socket {socket_path} --token {token} --state {state_path}"
    )
    provider_factory = provider_factory or _load_provider_factory()
    provider = provider_factory(seed=seed)
    config = CoordinatorConfig(
        cwd=str(workspace.resolve()),
        task=(
            f"{contract.task_instruction} Evaluate each serious candidate with: {evaluate_command}. "
            f"The metric is {contract.metric_name} and {contract.metric_direction} is better. Restore "
            "the strongest development candidate, then declare it with: "
            f"{declare_command}. Held-out evaluation is unavailable."
        ),
        max_cycles=20,
        max_turns=max_turns,
        max_tree_depth=2,
        auto_git=False,
        require_base_branch=False,
        workspace_dir=str((output_dir / "arbor-session").resolve()),
        time_budget=timeout,
        skills_enabled=False,
        contamination_probe=False,
        export_trajectory=False,
        distill_skills=False,
    )
    config.search.enabled = False
    report = asyncio.run(CoordinatorOrchestrator(config=config, provider=provider).run())
    if not state_path.is_file():
        evaluate_current(socket_path, token, artifact_path, state_path)
    declared = declare_current(socket_path, token, state_path)
    payload = {
        "native_component": contract.native_component or "native-arbor-coordinator",
        "native_loop": "arbor.coordinator.orchestrator.CoordinatorOrchestrator.run",
        "report": report,
        "declared_revision_id": declared["revision_id"],
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
    parser.add_argument("--max-turns", type=int, default=96)
    args = parser.parse_args(argv)
    run_native_loop(
        workspace=args.workspace,
        output_dir=args.output_dir,
        socket_path=args.socket,
        token=args.token,
        seed=args.seed,
        timeout=args.timeout,
        max_turns=args.max_turns,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["run_native_loop"]
