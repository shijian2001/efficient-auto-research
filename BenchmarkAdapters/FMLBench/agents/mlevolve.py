"""MLEvolve FML adapter preserving AgentSearch/SearchNode UCT selection."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

from ...contracts import CommandSpec
from ...protocol import write_json_exclusive
from ...registry import ROOT
from .base import FMLAgentAdapter, FMLAgentLaunchContext, native_python_command
from .helpers import (
    apply_candidate_diff,
    evaluate_development,
    git,
    initialize_repository,
    prompt_for_diff,
    query_openai,
)


class MLEvolveFMLAdapter(FMLAgentAdapter):
    agent_id = "mlevolve"
    native_entrypoint = "engine.agent_search.AgentSearch + engine.node_selection.select_with_soft_switch"
    installation_probe_arguments = (
        "-c",
        "from engine.agent_search import AgentSearch; from engine.node_selection import select_with_soft_switch",
    )

    def installation_executable(self) -> Path | None:
        return ROOT / "BenchmarkAdapters/environments/agents/mlevolve-autoresearch/.venv/bin/python"

    def build_native_command(self, context: FMLAgentLaunchContext, prompt: str) -> CommandSpec:
        return native_python_command(
            context=context,
            prompt=prompt,
            python=self.installation_executable(),
            module="BenchmarkAdapters.FMLBench.agents.mlevolve",
            source_root=ROOT / "BenchmarkAdapters/environments/agents/mlevolve-autoresearch/.venv/agent-source",
            label="MLEvolve native UCT FML loop",
        )


def _config(seed: int, timeout: int, steps: int) -> SimpleNamespace:
    search = SimpleNamespace(
        num_drafts=3,
        num_improves=3,
        topk_early_k=5,
        topk_early_max_per_branch=2,
        topk_late_k=3,
        topk_late_max_per_branch=1,
        explore_switch_start=0.5,
        explore_switch_end=0.7,
        min_exploration_weight=0.2,
    )
    decay = SimpleNamespace(
        phase_ratios=(0.4, 0.8), exploration_constant=1.414, alpha=0.01, lower_bound=0.7
    )
    agent = SimpleNamespace(seed=seed, steps=steps, time_limit=timeout, search=search, decay=decay)
    return SimpleNamespace(agent=agent)


def run_native_loop(
    *, workspace: Path, output_dir: Path, task_input: str, dev_command: str,
    model: str, seed: int, timeout: int, max_steps: int,
) -> dict[str, object]:
    from engine import node_selection
    from engine.agent_search import AgentSearch
    from engine.search_node import SearchNode
    from utils.metric import MetricValue, WorstMetricValue

    class FMLAgentSearch(AgentSearch):
        def __init__(self, *, seed: int, timeout: int, steps: int):
            self.cfg = _config(seed, timeout, steps)
            self.acfg = self.cfg.agent
            self.scfg = self.acfg.search
            self.current_step = 0
            self.search_start_time = time.time()
            self.metric_maximize = True
            self.branch_all_nodes: dict[int, list[SearchNode]] = {}
            self.virtual_root = SearchNode(
                code="",
                plan="frozen FML baseline",
                stage="root",
                step=0,
                metric=WorstMetricValue(maximize=True),
                is_buggy=False,
                is_valid=True,
            )

        def is_root(self, node: SearchNode) -> bool:
            return node.id == self.virtual_root.id

        def select_parent(self) -> SearchNode:
            return node_selection.select_with_soft_switch(self)

        def add_node(self, *, parent: SearchNode, diff_text: str, reward: float, step: int) -> SearchNode:
            parent.expected_child_count += 1
            branch_id = max(self.branch_all_nodes, default=0) + 1 if self.is_root(parent) else int(parent.branch_id or 1)
            node = SearchNode(
                code=diff_text,
                plan=f"FML candidate from {parent.id}",
                stage="draft" if self.is_root(parent) else "improve",
                parent=parent,
                step=step,
                metric=MetricValue(reward, maximize=True),
                is_buggy=False,
                is_valid=True,
                branch_id=branch_id,
                lock=False,
            )
            self.branch_all_nodes.setdefault(branch_id, []).append(node)
            node.update_beta(parent.metric.value is None or reward > float(parent.metric.value))
            cursor: SearchNode | None = node
            while cursor is not None:
                cursor.update(reward)
                cursor = cursor.parent
            self.current_step = step
            return node

    task = json.loads(os.environ["FML_CANONICAL_TASK_JSON"])
    editable_paths = tuple(task["editable_paths"])
    metric_sign = 1 if task["metric_direction"] == "higher" else -1
    output_dir.mkdir(parents=True, exist_ok=True)
    baseline_commit = initialize_repository(workspace)
    search = FMLAgentSearch(seed=seed, timeout=timeout, steps=max_steps)
    commits = {search.virtual_root.id: baseline_commit}
    diffs: dict[str, str] = {}
    feedback: dict[str, dict[str, object]] = {}
    history: list[dict[str, object]] = []
    started = time.monotonic()
    for step in range(1, max_steps + 1):
        if time.monotonic() - started >= timeout:
            break
        parent = search.select_parent()
        parent_commit = commits.get(parent.id, baseline_commit)
        system, user = prompt_for_diff(
            task_input=task_input,
            strategy=f"MLEvolve UCT {'draft' if search.is_root(parent) else 'improve'}",
            editable_paths=editable_paths,
            parent_diff=diffs.get(parent.id),
            parent_feedback=feedback.get(parent.id),
            history=history,
        )
        try:
            response, usage = query_openai(system, user, model=model)
            from .helpers import extract_unified_diff

            diff_text = extract_unified_diff(response)
            commit = apply_candidate_diff(workspace, parent_commit, diff_text, editable_paths)
            dev = evaluate_development(workspace, dev_command)
            raw_metric = float(dev["metric"])
            node = search.add_node(parent=parent, diff_text=diff_text, reward=metric_sign * raw_metric, step=step)
            commits[node.id] = commit
            diffs[node.id] = diff_text
            feedback[node.id] = dev
            history.append(
                {"id": node.id, "parent_id": parent.id, "branch_id": node.branch_id, "metric": raw_metric, "token_usage": usage}
            )
        except Exception as exc:
            git("reset", "--hard", parent_commit, workspace=workspace, check=False)
            history.append({"parent_id": parent.id, "error": f"{type(exc).__name__}: {exc}"})
    candidates = [node for nodes in search.branch_all_nodes.values() for node in nodes]
    best = max(candidates, key=lambda node: float(node.metric.value), default=None)
    if best is not None:
        git("reset", "--hard", commits[best.id], workspace=workspace)
    payload = {
        "native_loop": "engine.agent_search.AgentSearch",
        "native_selection": "engine.node_selection.select_with_soft_switch",
        "attempts": history,
        "best_node": best.id if best else None,
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


__all__ = ["MLEvolveFMLAdapter", "run_native_loop"]
