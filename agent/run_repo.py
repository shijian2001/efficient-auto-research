"""
Native EAR entry point for repository tasks.

The MLE entry point (`agent/run.py`) is unchanged and remains the entry point
for MLE-Bench. This one runs the SAME Kernel Thompson Sampling search over the
repository domain: a candidate is a git diff on an allowlist of editable paths,
and its score comes from an evaluator supplied by whoever launches the agent.

EAR owns the control loop here. The launcher hands over a workspace, a task
description, the editable-path allowlist, a way to invoke the evaluator, and a
budget; EAR then decides on its own what to try, in what order, from which
parent, and when to stop. It has no knowledge of the benchmark harness behind
the evaluator command.

Usage:
    python agent/run_repo.py \
        --workspace /path/to/repo \
        --task-file /path/to/task.md \
        --editable terminus_2.py --editable templates/ \
        --eval-command "/path/to/dev_client.py --socket ... --token ..." \
        --output-dir /path/to/output \
        --timeout 43200 --max-steps 50
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("AutoResearch")


def command_evaluator(
    eval_command: str,
    *,
    score_field: str = "score",
    timeout: int | None = None,
):
    """Build an evaluator that shells out and reads a JSON score from stdout.

    This is one convenience implementation of the injected-evaluator contract;
    a caller embedding EAR as a library can pass any callable instead. The
    command's JSON payload is echoed back to the model verbatim as opaque
    feedback — EAR does not interpret any field other than the score.
    """
    from agent.engine.repo_domain import EvaluationResult

    argv = shlex.split(eval_command)

    def evaluate(workspace: Path) -> EvaluationResult:
        completed = subprocess.run(
            argv,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        if completed.returncode:
            detail = (completed.stderr or completed.stdout or "")[-2000:]
            raise RuntimeError(f"evaluation command failed: {detail}")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"evaluation command did not return JSON: {exc}") from exc
        if score_field not in payload:
            raise RuntimeError(
                f"evaluation payload has no {score_field!r} field: {sorted(payload)}"
            )
        return EvaluationResult(score=float(payload[score_field]), feedback=payload)

    return evaluate


def run_repo_search(
    *,
    workspace: Path,
    task_description: str,
    editable_paths: tuple[str, ...],
    evaluator,
    output_dir: Path,
    model: str,
    max_steps: int,
    timeout: int,
    metric_sign: int = 1,
    temperature: float | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """Run EAR's native KTS search over a repository task and report the outcome."""
    from agent.engine.domain import run_kts_search
    from agent.engine.graph import SearchGraph
    from agent.engine.repo_domain import RepoDomain, RepoTaskConfig

    if seed is not None:
        random.seed(seed)
        try:
            import numpy as np

            np.random.seed(seed)
        except Exception:  # pragma: no cover - numpy is a hard dependency
            pass

    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()

    domain = RepoDomain(
        RepoTaskConfig(
            workspace=workspace,
            task_description=task_description,
            editable_paths=editable_paths,
            evaluator=evaluator,
            model=model,
            metric_sign=metric_sign,
            temperature=temperature,
        )
    )
    graph = SearchGraph()

    report_path = output_dir / "repo-report.json"
    step_log: list[dict[str, Any]] = []

    def write_report(payload: dict[str, Any]) -> None:
        report_path.write_text(json.dumps(payload, indent=2, sort_keys=True))

    # Stream progress to disk after every step so a run killed by the outer
    # timeout still leaves a complete trace of what it tried.
    def record_step(step_record: dict[str, Any]) -> None:
        step_log.append(step_record)
        write_report(
            {
                "mode": "repo",
                "in_progress": True,
                "baseline_commit": domain.baseline_commit,
                "step_log": step_log,
                "attempts": domain.history,
            }
        )

    domain.on_step_recorded = record_step  # type: ignore[method-assign]

    summary = run_kts_search(
        domain=domain,
        graph=graph,
        max_steps=max_steps,
        time_limit=timeout,
        start_time=started,
    )

    # Leave the workspace on the best-scoring candidate: for a repository task
    # the final artifact IS the working tree.
    final_commit = domain.restore_best()
    in_tokens, out_tokens = domain.token_usage()

    payload: dict[str, Any] = {
        "mode": "repo",
        "native_loop": "EAR Kernel Thompson Sampling repository search",
        "native_selection": "agent.engine.thompson.select_parent",
        "baseline_commit": domain.baseline_commit,
        "final_commit": final_commit,
        "best_attempt": summary.best_attempt.id if summary.best_attempt else None,
        "best_score": summary.best_metric,
        "steps_taken": summary.steps_taken,
        "stopped_reason": summary.stopped_reason,
        "elapsed_seconds": summary.elapsed_seconds,
        "total_in_tokens": in_tokens,
        "total_out_tokens": out_tokens,
        "total_tokens": in_tokens + out_tokens,
        "attempts": domain.history,
        "step_log": summary.step_log,
        "in_progress": False,
    }
    write_report(payload)
    logger.info(
        f"Repo search finished: best={summary.best_metric} after "
        f"{summary.steps_taken} steps ({summary.stopped_reason})"
    )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AutoResearch Agent (repository mode)")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--task-file", type=Path)
    parser.add_argument("--task-text", type=str)
    parser.add_argument(
        "--editable",
        action="append",
        default=[],
        required=False,
        help="Relative path the agent may modify (repeatable).",
    )
    parser.add_argument("--eval-command", required=True)
    parser.add_argument("--eval-score-field", default="score")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--max-steps", type=int, default=50)
    parser.add_argument("--timeout", type=int, default=43200)
    parser.add_argument(
        "--metric-direction",
        choices=("maximize", "minimize"),
        default="maximize",
        help="Direction of the evaluator score.",
    )
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args(argv)

    if not args.editable:
        parser.error("--editable must be given at least once")

    workspace = args.workspace.resolve()
    if args.task_text:
        task_description = args.task_text
    elif args.task_file and args.task_file.exists():
        task_description = args.task_file.read_text(encoding="utf-8")
    else:
        parser.error("one of --task-text or an existing --task-file is required")

    if args.seed is not None:
        os.environ.setdefault("EAR_SEED", str(args.seed))

    run_repo_search(
        workspace=workspace,
        task_description=task_description,
        editable_paths=tuple(args.editable),
        evaluator=command_evaluator(
            args.eval_command, score_field=args.eval_score_field
        ),
        output_dir=args.output_dir.resolve(),
        model=args.model or "gpt-5.5",
        max_steps=args.max_steps,
        timeout=args.timeout,
        metric_sign=1 if args.metric_direction == "maximize" else -1,
        temperature=args.temperature,
        seed=args.seed,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["command_evaluator", "run_repo_search"]
