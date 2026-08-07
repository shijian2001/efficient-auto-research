"""MLEvolve repository-domain backend preserving its native UCT search tree."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

from engine import node_selection
from engine.agent_search import AgentSearch
from engine.search_node import SearchNode
from llm import query as llm_query
from utils.metric import MetricValue, WorstMetricValue

from .repository_tools import (
    apply_candidate_diff,
    candidate_prompt,
    commit_for_head,
    evaluate_dev,
    extract_unified_diff,
    git,
)


def _config(model: str, seed: int, timeout: int, steps: int):
    stage = SimpleNamespace(
        model=model,
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        base_url=os.environ.get("OPENAI_BASE_URL", ""),
    )
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
        phase_ratios=(0.4, 0.8),
        exploration_constant=1.414,
        alpha=0.01,
        lower_bound=0.7,
    )
    agent = SimpleNamespace(
        seed=seed,
        steps=steps,
        time_limit=timeout,
        code=stage,
        feedback=stage,
        search=search,
        decay=decay,
    )
    return SimpleNamespace(agent=agent)


class RepositoryAgentSearch(AgentSearch):
    """Repository specialization retaining MLEvolve's SearchNode and UCT selector."""

    def __init__(self, *, model: str, seed: int, timeout: int, steps: int):
        self.cfg = _config(model, seed, timeout, steps)
        self.acfg = self.cfg.agent
        self.scfg = self.acfg.search
        self.current_step = 0
        self.search_start_time = time.time()
        self.metric_maximize = True
        self.branch_all_nodes: dict[int, list[SearchNode]] = {}
        self.virtual_root = SearchNode(
            code="",
            plan="frozen terminus-2 baseline",
            stage="root",
            step=0,
            metric=WorstMetricValue(maximize=True),
            is_buggy=False,
            is_valid=True,
        )

    def is_root(self, node: SearchNode) -> bool:
        return node.id == self.virtual_root.id

    def select_repository_parent(self) -> SearchNode:
        return node_selection.select_with_soft_switch(self)

    def add_repository_node(
        self,
        *,
        parent: SearchNode,
        diff_text: str,
        reward: float,
        step: int,
    ) -> SearchNode:
        parent.expected_child_count += 1
        branch_id = (
            max(self.branch_all_nodes, default=0) + 1
            if self.is_root(parent)
            else int(parent.branch_id or 1)
        )
        node = SearchNode(
            code=diff_text,
            plan=f"repository candidate from {parent.id}",
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
        improved = parent.metric.value is None or reward > float(parent.metric.value)
        node.update_beta(improved)
        cursor: SearchNode | None = node
        while cursor is not None:
            cursor.update(reward)
            cursor = cursor.parent
        self.current_step = step
        return node


def run_native_loop(
    *,
    workspace: Path,
    output_dir: Path,
    dev_command: str,
    model: str,
    seed: int,
    timeout: int,
    max_steps: int = 50,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "native-result.json"
    if result_path.exists():
        raise RuntimeError(f"refusing to overwrite MLEvolve result: {result_path}")
    search = RepositoryAgentSearch(model=model, seed=seed, timeout=timeout, steps=max_steps)
    baseline_commit = commit_for_head(workspace)
    commits = {search.virtual_root.id: baseline_commit}
    diffs: dict[str, str] = {}
    feedback: dict[str, dict[str, object]] = {}
    history: list[dict[str, object]] = []
    started = time.monotonic()
    for step in range(1, max_steps + 1):
        if time.monotonic() - started >= timeout:
            break
        parent = search.select_repository_parent()
        parent_commit = commits.get(parent.id, baseline_commit)
        system, user = candidate_prompt(
            strategy=f"MLEvolve UCT {'draft' if search.is_root(parent) else 'improve'} from node {parent.id}",
            parent_diff=diffs.get(parent.id),
            parent_feedback=feedback.get(parent.id),
            history=history,
        )
        try:
            response = llm_query(
                system,
                user,
                model=model,
                temperature=1.0,
                max_tokens=65536,
                cfg=search.cfg,
            )
            diff_text = extract_unified_diff(str(response))
            commit = apply_candidate_diff(workspace, parent_commit, diff_text)
            dev = evaluate_dev(workspace, dev_command)
            reward = float(dev["pass_rate"])
            node = search.add_repository_node(
                parent=parent,
                diff_text=diff_text,
                reward=reward,
                step=step,
            )
            commits[node.id] = commit
            diffs[node.id] = diff_text
            feedback[node.id] = dev
            history.append(
                {
                    "id": node.id,
                    "parent_id": parent.id,
                    "branch_id": node.branch_id,
                    "reward": reward,
                    "uct": node.uct_value(),
                }
            )
        except Exception as exc:
            git("reset", "--hard", parent_commit, workspace=workspace, check=False)
            history.append(
                {"parent_id": parent.id, "error": f"{type(exc).__name__}: {exc}"}
            )
    candidates = [node for nodes in search.branch_all_nodes.values() for node in nodes]
    best = max(candidates, key=lambda node: float(node.metric.value), default=None)
    if best is not None:
        git("reset", "--hard", commits[best.id], workspace=workspace)
    payload = {
        "native_loop": "engine.agent_search.AgentSearch",
        "native_selection": "engine.node_selection.select_with_soft_switch",
        "attempts": history,
        "best_node": best.id if best else None,
        "best_dev_pass_rate": best.metric.value if best else None,
        "elapsed_seconds": time.monotonic() - started,
    }
    result_path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
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
    run_native_loop(**vars(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["RepositoryAgentSearch", "run_native_loop"]
