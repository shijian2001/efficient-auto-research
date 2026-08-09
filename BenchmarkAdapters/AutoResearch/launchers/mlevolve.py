"""MLEvolve Autoresearch backend preserving AgentSearch/SearchNode/UCT selection."""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from types import SimpleNamespace

from engine import node_selection
from engine.agent_search import AgentSearch
from engine.search_node import SearchNode
from utils.metric import MetricValue, WorstMetricValue

from ...autonomous_optimization import task_contract
from ...protocol import write_json_exclusive
from ..dev_client import declare_current, evaluate_current
from .proposal import ProposalProvider, command_proposal_provider


def _config(seed: int, timeout: int, steps: int):
    search = SimpleNamespace(
        num_drafts=3,
        num_improves=3,
        num_bugs=1,
        topk_early_k=5,
        topk_early_max_per_branch=2,
        topk_late_k=3,
        topk_late_max_per_branch=1,
        topk_max_improves=10,
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
        search=search,
        decay=decay,
        use_aggregation=False,
        branch_fusion_trigger_prob=0.0,
    )
    return SimpleNamespace(agent=agent)


class ArchitectureAgentSearch(AgentSearch):
    def __init__(self, *, seed: int, timeout: int, steps: int) -> None:
        self.cfg = _config(seed, timeout, steps)
        self.acfg = self.cfg.agent
        self.scfg = self.acfg.search
        self.current_step = 0
        self.search_start_time = time.time()
        self.metric_maximize = False
        self.branch_all_nodes: dict[int, list[SearchNode]] = {}
        self.virtual_root = SearchNode(
            code="",
            plan="frozen Autoresearch train.py baseline",
            stage="root",
            step=0,
            metric=WorstMetricValue(maximize=False),
            is_buggy=False,
            is_valid=True,
        )

    def is_root(self, node: SearchNode) -> bool:
        return node.id == self.virtual_root.id

    def select_parent(self) -> SearchNode:
        return node_selection.select_with_soft_switch(self)

    def add_candidate(
        self,
        *,
        parent: SearchNode,
        source: str,
        plan: str,
        score: float | None,
        step: int,
    ) -> SearchNode:
        parent.expected_child_count += 1
        branch_id = (
            max(self.branch_all_nodes, default=0) + 1
            if self.is_root(parent)
            else int(parent.branch_id or 1)
        )
        node = SearchNode(
            code=source,
            plan=plan,
            stage="draft" if self.is_root(parent) else "improve",
            parent=parent,
            step=step,
            metric=MetricValue(score, maximize=False) if score is not None else WorstMetricValue(maximize=False),
            is_buggy=score is None,
            is_valid=score is not None,
            branch_id=branch_id,
            lock=False,
        )
        self.branch_all_nodes.setdefault(branch_id, []).append(node)
        parent_score = parent.metric.value if parent.metric else None
        node.update_beta(score is not None and (parent_score is None or score < float(parent_score)))
        reward = -score if score is not None else -1e9
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
    socket_path: str,
    token: str,
    seed: int,
    timeout: int,
    max_steps: int = 50,
    proposer: ProposalProvider = command_proposal_provider,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    contract = task_contract()
    random.seed(seed)
    state_path = contract.state_path(workspace)
    artifact_path = contract.artifact_path(workspace)
    baseline_source = artifact_path.read_text(encoding="utf-8")
    search = ArchitectureAgentSearch(seed=seed, timeout=timeout, steps=max_steps)
    sources = {search.virtual_root.id: baseline_source}
    revision_ids = {search.virtual_root.id: "baseline"}
    feedback_by_node: dict[str, dict[str, object]] = {}
    history: list[dict[str, object]] = []
    started = time.monotonic()
    for step in range(1, max_steps + 1):
        if time.monotonic() - started >= timeout:
            break
        parent = search.select_parent()
        parent_source = sources[parent.id]
        try:
            proposal = proposer(
                {
                    "agent": "mlevolve",
                    "native_scheduler": "engine.node_selection.select_with_soft_switch",
                    "native_stage": "draft" if search.is_root(parent) else "improve",
                    "task_name": contract.task_name,
                    "metric_name": contract.metric_name,
                    "metric_direction": contract.metric_direction,
                    "outer_seed": seed,
                    "step": step,
                    "parent_node_id": parent.id,
                    "program": contract.program_path(workspace).read_text(encoding="utf-8"),
                    "parent_train_source": parent_source,
                    "parent_feedback": feedback_by_node.get(parent.id),
                    "history": history,
                }
            )
            artifact_path.write_text(proposal.train_source, encoding="utf-8")
            feedback = evaluate_current(
                socket_path,
                token,
                artifact_path,
                state_path,
                revision_ids[parent.id],
            )
            state = json.loads(state_path.read_text(encoding="utf-8"))
            score = contract.score(feedback)
            node = search.add_candidate(
                parent=parent,
                source=proposal.train_source,
                plan=proposal.plan,
                score=score,
                step=step,
            )
            sources[node.id] = proposal.train_source
            revision_ids[node.id] = str(state["revision_id"])
            feedback_by_node[node.id] = feedback
            history.append(
                {
                    "node_id": node.id,
                    "parent_node_id": parent.id,
                    "branch_id": node.branch_id,
                    contract.metric_name: score,
                    "status": feedback["status"],
                }
            )
        except Exception as exc:
            artifact_path.write_text(parent_source, encoding="utf-8")
            history.append(
                {"parent_node_id": parent.id, "error": f"{type(exc).__name__}: {exc}"}
            )
    candidates = [node for nodes in search.branch_all_nodes.values() for node in nodes]
    valid = [node for node in candidates if node.metric and node.metric.value is not None]
    best = min(valid, key=lambda node: float(node.metric.value), default=None)
    declared = None
    if best is not None:
        artifact_path.write_text(sources[best.id], encoding="utf-8")
        evaluate_current(
            socket_path,
            token,
            artifact_path,
            state_path,
            revision_ids[best.id],
        )
        declared = declare_current(socket_path, token, state_path)
    payload = {
        "native_component": contract.native_component or "native-mlevolve-uct",
        "native_loop": "engine.agent_search.AgentSearch + SearchNode",
        "native_selection": "engine.node_selection.select_with_soft_switch",
        "metric_direction": contract.metric_direction,
        "best_node_id": best.id if best else None,
        "declared_revision_id": declared["revision_id"] if declared else None,
        "attempts": history,
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
    parser.add_argument("--max-steps", type=int, default=50)
    args = parser.parse_args(argv)
    payload = run_native_loop(
        workspace=args.workspace,
        output_dir=args.output_dir,
        socket_path=args.socket,
        token=args.token,
        seed=args.seed,
        timeout=args.timeout,
        max_steps=args.max_steps,
    )
    return 0 if payload["declared_revision_id"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["ArchitectureAgentSearch", "run_native_loop"]
