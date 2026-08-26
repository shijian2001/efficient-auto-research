"""Stable evaluation wrapper used inside generated MLE workspaces."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .common import (
    AdapterError,
    parse_metric_with_text,
    sha256_file,
    validate_submission_remote,
    write_json,
)
from .state_store import find_bound_record, run_id_from_environment, state_root_from_environment


def _load_metadata(workspace: Path) -> dict:
    path = workspace / ".mle" / "adapter.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterError(f"invalid adapter metadata: {path}") from exc


def _receipt_path(workspace: Path, metadata: dict) -> Path:
    relative = str(metadata.get("evaluation_receipt_path", "results/mle_eval_receipt.json"))
    path = workspace / relative
    try:
        path.resolve().relative_to(workspace.resolve())
    except ValueError as exc:
        raise AdapterError("evaluation receipt path must remain inside the workspace") from exc
    return path


def _check_immutable(workspace: Path, metadata: dict) -> None:
    for relative, expected in metadata.get("immutable", {}).items():
        path = workspace / relative
        if not path.is_file() or sha256_file(path) != expected:
            raise AdapterError(f"protected adapter input changed: {relative}")


def _validate(workspace: Path, metadata: dict, submission: Path) -> None:
    valid, message = validate_submission_remote(
        metadata["validation_url"], metadata["competition_id"], submission
    )
    if not valid:
        raise AdapterError(message)


def _remove_file(path: Path, *, label: str) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        raise AdapterError(f"{label} must be a file, not a directory: {path}")
    try:
        path.unlink()
    except OSError as exc:
        raise AdapterError(f"could not remove stale {label}: {path}") from exc


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_candidate(workspace: Path) -> float:
    metadata = _load_metadata(workspace)
    _check_immutable(workspace, metadata)
    solution = workspace / metadata.get("solution_path", "solution.py")
    submission = workspace / metadata.get("submission_path", "submission.csv")
    receipt_path = _receipt_path(workspace, metadata)
    if not solution.is_file():
        raise AdapterError(f"candidate entrypoint is missing: {solution.name}")
    if solution.is_symlink():
        raise AdapterError("candidate entrypoint must be a regular file, not a symlink")

    # Fail closed across repeated evaluations in the same worktree. A previous
    # submission or state must never be paired with a new self-reported metric.
    preexisting = submission.exists() or submission.is_symlink()
    preexisting_hash = (
        sha256_file(submission)
        if preexisting and submission.is_file() and not submission.is_symlink()
        else None
    )
    _remove_file(receipt_path, label="evaluation receipt")
    _remove_file(submission, label="submission")

    solution_hash = sha256_file(solution)
    started_at = _utc_now()
    started_monotonic = time.perf_counter()
    env = os.environ.copy()
    # Host-owned state is controller-only. Never expose its writable path or
    # run identity to untrusted candidate code.
    for name in ("ARBOR_MLE_STATE_ROOT", "ARBOR_MLE_RUN_ID", "ARBOR_MLE_NODE_ID"):
        env.pop(name, None)
    env.update(
        {
            "MLE_COMPETITION_ID": metadata["competition_id"],
            "MLE_DATA_DIR": str(workspace / "input"),
            "MLE_SUBMISSION_PATH": str(submission),
            "MLE_METRIC_DIRECTION": metadata["metric_direction"],
        }
    )
    process = subprocess.Popen(
        [sys.executable, str(solution)],
        cwd=workspace,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    metric_lines: list[str] = []
    log_path = workspace / ".arbor" / "mle_solution.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log_handle:
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log_handle.write(line)
            if "METRIC" in line:
                metric_lines.append(line)
    return_code = process.wait()
    execution_finished_at = _utc_now()
    execution_seconds = time.perf_counter() - started_monotonic
    if return_code != 0:
        raise AdapterError(f"solution.py exited with code {return_code}")
    raw_metric_text, metric = parse_metric_with_text("".join(metric_lines))
    if not submission.is_file() or submission.is_symlink():
        raise AdapterError("solution.py did not create a regular submission.csv file")
    if sha256_file(solution) != solution_hash:
        raise AdapterError("solution.py modified itself during execution")
    _validate(workspace, metadata, submission)
    submission_hash = sha256_file(submission)
    submission_stat = submission.stat()
    solution_stat = solution.stat()
    write_json(
        receipt_path,
        {
            "schema_version": 3,
            "competition_id": metadata["competition_id"],
            "metric": metric,
            "raw_metric_text": raw_metric_text,
            "raw_metric_numeric": metric,
            "metric_direction": metadata["metric_direction"],
            "evaluation_status": "verified",
            "evaluation_semantics": "candidate_local_metric_plus_format_validation",
            "official_grading": False,
            "format_validated": True,
            "solution_sha256": solution_hash,
            "solution_size": solution_stat.st_size,
            "solution_mtime_ns": solution_stat.st_mtime_ns,
            "submission_sha256": submission_hash,
            "submission_size": submission_stat.st_size,
            "submission_mtime_ns": submission_stat.st_mtime_ns,
            "submission_target_preexisting": preexisting,
            "submission_preexisting_sha256": preexisting_hash,
            "submission_created_after_cleanup": True,
            "started_at": started_at,
            "execution_finished_at": execution_finished_at,
            "evaluation_finished_at": _utc_now(),
            "execution_seconds": execution_seconds,
        },
    )
    return metric


def verify_candidate(workspace: Path) -> float:
    metadata = _load_metadata(workspace)
    _check_immutable(workspace, metadata)
    solution = workspace / metadata.get("solution_path", "solution.py")
    submission = workspace / metadata.get("submission_path", "submission.csv")
    record = find_bound_record(
        root=state_root_from_environment(),
        run_id=run_id_from_environment(),
        competition_id=str(metadata["competition_id"]),
        solution=solution,
        submission=submission,
        node_id=os.environ.get("ARBOR_MLE_NODE_ID") or None,
    )
    if record.get("metric_direction") != metadata.get("metric_direction"):
        raise AdapterError("host-owned MLE record metric direction does not match the adapter")
    try:
        metric = float(record["raw_metric_numeric"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AdapterError("host-owned MLE record local metric is invalid") from exc
    if not math.isfinite(metric):
        raise AdapterError("host-owned MLE record local metric must be finite")
    return metric


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("run", "verify"), nargs="?", default="run")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--node-id")
    args = parser.parse_args(argv)
    try:
        if args.node_id:
            os.environ["ARBOR_MLE_NODE_ID"] = args.node_id
        metric = (
            run_candidate(args.workspace.resolve())
            if args.mode == "run"
            else verify_candidate(args.workspace.resolve())
        )
    except AdapterError as exc:
        print(f"MLE adapter evaluation failed: {exc}", file=sys.stderr)
        return 2
    if args.mode == "verify":
        print(
            json.dumps(
                {
                    "score": metric,
                    "evaluation_role": "artifact_verification_only",
                    "official_grading": False,
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
