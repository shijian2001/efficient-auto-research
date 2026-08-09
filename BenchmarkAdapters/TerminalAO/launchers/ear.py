"""EAR repository-domain backend preserving its native KTS scheduler."""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from pathlib import Path

import numpy as np

from agent.engine.graph import Attempt, SearchGraph
from agent.engine.thompson import select_parent
from agent.llm import query as llm_query

from ...protocol import write_json_exclusive
from .repository_tools import (
    apply_candidate_diff,
    candidate_prompt,
    commit_for_head,
    embedding_for_text,
    evaluate_dev,
    extract_unified_diff,
    git,
)
from .model_config import outer_model_parameters


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
        raise RuntimeError(f"refusing to overwrite EAR result: {result_path}")
    random.seed(seed)
    np.random.seed(seed)
    graph = SearchGraph()
    baseline_commit = commit_for_head(workspace)
    commits: dict[str, str] = {}
    diffs: dict[str, str] = {}
    feedback: dict[str, dict[str, object]] = {}
    history: list[dict[str, object]] = []
    started = time.monotonic()
    model_parameters = outer_model_parameters()
    stagnation = 0
    best_reward: float | None = None
    for step in range(max_steps):
        elapsed = time.monotonic() - started
        if elapsed >= timeout:
            break
        parent_id = select_parent(graph, stagnation=stagnation, metric_sign=1)
        parent = graph.attempts.get(parent_id) if parent_id else None
        parent_commit = commits.get(parent.id, baseline_commit) if parent else baseline_commit
        strategy = "new-root" if parent is None else "g3-kts-improve"
        system, user = candidate_prompt(
            strategy=strategy,
            parent_diff=diffs.get(parent.id) if parent else None,
            parent_feedback=feedback.get(parent.id) if parent else None,
            history=history,
        )
        attempt_started = time.monotonic()
        try:
            query_parameters = {}
            if model_parameters.get("temperature") is not None:
                query_parameters["temperature"] = float(model_parameters["temperature"])
            response, input_tokens, output_tokens = llm_query(
                system, user, model=model, **query_parameters
            )
            diff_text = extract_unified_diff(response)
            commit = apply_candidate_diff(workspace, parent_commit, diff_text)
            dev = evaluate_dev(workspace, dev_command)
            reward = float(dev["pass_rate"])
            attempt = Attempt(
                id=f"ear-{step:04d}",
                plan=strategy,
                code=diff_text,
                metric=reward,
                parent_id=parent.id if parent else None,
                embedding=embedding_for_text(diff_text),
            )
            graph.add_attempt(attempt)
            commits[attempt.id] = commit
            diffs[attempt.id] = diff_text
            feedback[attempt.id] = dev
            if best_reward is None or reward > best_reward:
                best_reward = reward
                stagnation = 0
            else:
                stagnation += 1
            history.append(
                {
                    "id": attempt.id,
                    "reward": reward,
                    "parent_id": parent_id,
                    "strategy": strategy,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "elapsed_seconds": time.monotonic() - attempt_started,
                    "candidate_digest": str(dev["candidate_digest"]),
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
            history.append(
                {"id": attempt.id, "error": attempt.error, "parent_id": parent_id, "strategy": strategy}
            )
    best = max(
        (attempt for attempt in graph.attempts.values() if attempt.metric is not None),
        key=lambda attempt: float(attempt.metric),
        default=None,
    )
    if best is not None:
        git("reset", "--hard", commits[best.id], workspace=workspace)
    payload = {
        "native_loop": "EAR G3 repository loop",
        "native_selection": "agent.engine.thompson.select_parent",
        "attempts": history,
        "best_attempt": best.id if best else None,
        "best_dev_pass_rate": best.metric if best else None,
        "elapsed_seconds": time.monotonic() - started,
    }
    write_json_exclusive(result_path, payload)
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
    os.environ["EAR_SEED"] = str(args.seed)
    run_native_loop(**vars(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["run_native_loop"]
