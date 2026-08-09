"""EAR FML adapter using the native G3 Kernel Thompson scheduler."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from ...contracts import CommandSpec
from ...protocol import write_json_exclusive
from ...registry import ROOT
from .base import FMLAgentAdapter, FMLAgentLaunchContext, native_python_command
from .helpers import (
    apply_candidate_diff,
    embedding,
    evaluate_development,
    git,
    initialize_repository,
    prompt_for_diff,
    query_openai,
)


class EARFMLAdapter(FMLAgentAdapter):
    agent_id = "ear"
    native_entrypoint = "agent.engine.thompson.select_parent + FML repository bridge"
    installation_probe_arguments = ("-c", "from agent.engine.thompson import select_parent")

    def installation_executable(self) -> Path | None:
        return ROOT / "BenchmarkAdapters/environments/agents/ear-autoresearch/.venv/bin/python"

    def build_native_command(self, context: FMLAgentLaunchContext, prompt: str) -> CommandSpec:
        return native_python_command(
            context=context,
            prompt=prompt,
            python=self.installation_executable(),
            module="BenchmarkAdapters.FMLBench.agents.ear",
            source_root=ROOT / "BenchmarkAdapters/environments/agents/ear-autoresearch/.venv/agent-source",
            label="EAR native G3 KTS FML loop",
        )


def run_native_loop(
    *,
    workspace: Path,
    output_dir: Path,
    task_input: str,
    dev_command: str,
    model: str,
    seed: int,
    timeout: int,
    max_steps: int,
) -> dict[str, object]:
    from agent.engine.graph import Attempt, SearchGraph
    from agent.engine.thompson import select_parent

    del seed
    task = json.loads(os.environ["FML_CANONICAL_TASK_JSON"])
    editable_paths = tuple(task["editable_paths"])
    metric_sign = 1 if task["metric_direction"] == "higher" else -1
    output_dir.mkdir(parents=True, exist_ok=True)
    baseline_commit = initialize_repository(workspace)
    graph = SearchGraph()
    commits: dict[str, str] = {}
    diffs: dict[str, str] = {}
    feedback: dict[str, dict[str, object]] = {}
    history: list[dict[str, object]] = []
    started = time.monotonic()
    stagnation = 0
    for step in range(max_steps):
        if time.monotonic() - started >= timeout:
            break
        parent_id = select_parent(graph, stagnation=stagnation, metric_sign=1)
        parent = graph.attempts.get(parent_id) if parent_id else None
        parent_commit = commits.get(parent.id, baseline_commit) if parent else baseline_commit
        strategy = "new-root" if parent is None else "g3-kts-improve"
        system, user = prompt_for_diff(
            task_input=task_input,
            strategy=strategy,
            editable_paths=editable_paths,
            parent_diff=diffs.get(parent.id) if parent else None,
            parent_feedback=feedback.get(parent.id) if parent else None,
            history=history,
        )
        try:
            response, usage = query_openai(system, user, model=model)
            from .helpers import extract_unified_diff

            diff_text = extract_unified_diff(response)
            commit = apply_candidate_diff(workspace, parent_commit, diff_text, editable_paths)
            dev = evaluate_development(workspace, dev_command)
            raw_metric = float(dev["metric"])
            reward = metric_sign * raw_metric
            attempt = Attempt(
                id=f"ear-{step:04d}",
                plan=strategy,
                code=diff_text,
                metric=reward,
                parent_id=parent.id if parent else None,
                embedding=embedding(diff_text),
            )
            graph.add_attempt(attempt)
            commits[attempt.id] = commit
            diffs[attempt.id] = diff_text
            feedback[attempt.id] = dev
            previous_best = max(
                (float(value.metric) for value in graph.attempts.values() if value.metric is not None),
                default=reward,
            )
            stagnation = 0 if reward >= previous_best else stagnation + 1
            history.append(
                {
                    "id": attempt.id,
                    "parent_id": parent_id,
                    "metric": raw_metric,
                    "reward": reward,
                    "candidate_digest": dev["candidate_digest"],
                    "token_usage": usage,
                }
            )
        except Exception as exc:
            git("reset", "--hard", parent_commit, workspace=workspace, check=False)
            attempt = Attempt(
                id=f"ear-{step:04d}",
                plan=strategy,
                code="",
                error=f"{type(exc).__name__}: {exc}",
                parent_id=parent.id if parent else None,
            )
            graph.add_attempt(attempt)
            stagnation += 1
            history.append({"id": attempt.id, "error": attempt.error})
    best = max(
        (value for value in graph.attempts.values() if value.metric is not None),
        key=lambda value: float(value.metric),
        default=None,
    )
    if best is not None:
        git("reset", "--hard", commits[best.id], workspace=workspace)
    payload = {
        "native_loop": "agent.engine.thompson.select_parent",
        "variant": os.environ["FML_AGENT_VARIANT"],
        "attempts": history,
        "best_attempt": best.id if best else None,
        "elapsed_seconds": time.monotonic() - started,
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


__all__ = ["EARFMLAdapter", "run_native_loop"]
