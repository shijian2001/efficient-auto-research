"""EAR Autoresearch backend preserving the native G3 KTS scheduler."""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import numpy as np
from agent.engine.graph import Attempt, SearchGraph
from agent.engine.thompson import select_parent

from ...autonomous_optimization import task_contract
from ...protocol import write_json_exclusive
from ..dev_client import declare_current, evaluate_current
from .proposal import ProposalProvider, command_proposal_provider


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
    state_path = contract.state_path(workspace)
    random.seed(seed)
    np.random.seed(seed)
    graph = SearchGraph()
    sources: dict[str, str] = {}
    revision_ids: dict[str, str] = {}
    feedback_by_attempt: dict[str, dict[str, object]] = {}
    history: list[dict[str, object]] = []
    artifact_path = contract.artifact_path(workspace)
    baseline_source = artifact_path.read_text(encoding="utf-8")
    started = time.monotonic()
    stagnation = 0
    best_score: float | None = None
    for step in range(max_steps):
        if time.monotonic() - started >= timeout:
            break
        parent_id = select_parent(graph, stagnation=stagnation, metric_sign=contract.metric_sign)
        parent_source = sources.get(parent_id, baseline_source)
        parent_feedback = feedback_by_attempt.get(parent_id) if parent_id else None
        try:
            proposal = proposer(
                {
                    "agent": "ear",
                    "native_scheduler": "agent.engine.thompson.select_parent",
                    "task_name": contract.task_name,
                    "metric_name": contract.metric_name,
                    "metric_direction": contract.metric_direction,
                    "outer_seed": seed,
                    "step": step,
                    "parent_attempt_id": parent_id,
                    "parent_revision_id": revision_ids.get(parent_id) if parent_id else "baseline",
                    "program": contract.program_path(workspace).read_text(encoding="utf-8"),
                    "parent_train_source": parent_source,
                    "parent_feedback": parent_feedback,
                    "history": history,
                }
            )
            artifact_path.write_text(proposal.train_source, encoding="utf-8")
            feedback = evaluate_current(
                socket_path,
                token,
                artifact_path,
                state_path,
                revision_ids.get(parent_id, "baseline"),
            )
            state = json.loads(state_path.read_text(encoding="utf-8"))
            score = contract.score(feedback)
            attempt = Attempt(
                id=f"ear-{step:04d}",
                plan=proposal.plan,
                code=proposal.train_source,
                metric=score,
                parent_id=parent_id,
                embedding=proposal.embedding,
            )
            graph.add_attempt(attempt)
            sources[attempt.id] = proposal.train_source
            revision_ids[attempt.id] = str(state["revision_id"])
            feedback_by_attempt[attempt.id] = feedback
            if score is not None and (best_score is None or score < best_score):
                best_score = score
                stagnation = 0
            else:
                stagnation += 1
            history.append(
                {
                    "attempt_id": attempt.id,
                    "parent_attempt_id": parent_id,
                    "revision_id": state["revision_id"],
                    contract.metric_name: score,
                    "status": feedback["status"],
                }
            )
        except Exception as exc:
            artifact_path.write_text(parent_source, encoding="utf-8")
            graph.add_attempt(
                Attempt(
                    id=f"ear-{step:04d}",
                    plan="proposal-or-evaluation-failed",
                    code="",
                    error=f"{type(exc).__name__}: {exc}",
                    parent_id=parent_id,
                )
            )
            history.append(
                {
                    "attempt_id": f"ear-{step:04d}",
                    "parent_attempt_id": parent_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            stagnation += 1
    best = min(
        (attempt for attempt in graph.attempts.values() if attempt.metric is not None),
        key=lambda attempt: float(attempt.metric),
        default=None,
    )
    declared = None
    if best is not None:
        artifact_path.write_text(sources[best.id], encoding="utf-8")
        feedback = evaluate_current(
            socket_path,
            token,
            artifact_path,
            state_path,
            revision_ids[best.id],
        )
        declared = declare_current(socket_path, token, state_path)
        history.append(
            {
                "attempt_id": "final-replay",
                "source_attempt_id": best.id,
                "revision_id": declared["revision_id"],
                contract.metric_name: feedback[contract.metric_name],
            }
        )
    payload = {
        "native_component": contract.native_component or "native-ear-kts",
        "native_loop": "EAR G3 SearchGraph + Kernel Thompson Sampling",
        "native_selection": "agent.engine.thompson.select_parent(metric_sign=-1)",
        "best_attempt_id": best.id if best else None,
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


__all__ = ["run_native_loop"]
