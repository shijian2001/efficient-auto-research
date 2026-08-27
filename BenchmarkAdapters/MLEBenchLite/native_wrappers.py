"""Deterministic final-artifact wrappers around native MLE launchers."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


# Seconds allowed for the launcher to exit on SIGTERM before it is killed, and
# the slice of the outer budget+120s window reserved for copying the artifact.
_GRACE_SECONDS = 20.0
_CLOSEOUT_SECONDS = 60.0


def _copy_exclusive(source: Path, destination: Path) -> None:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file() or source.is_symlink() or source.stat().st_size <= 0:
        raise RuntimeError(f"native launcher did not produce a regular final artifact: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open("rb") as input_handle, destination.open("xb") as output_handle:
            shutil.copyfileobj(input_handle, output_handle)
            output_handle.flush()
            os.fsync(output_handle.fileno())
    except FileExistsError as exc:
        raise RuntimeError(f"refusing to overwrite native final artifact: {destination}") from exc


def _run_native(argv: list[str], *, budget_seconds: float | None = None) -> None:
    """Run the native launcher, stopping it in time to still publish its artifact.

    The outer adapter kills this whole process tree at budget+120s. That backstop
    is deliberately blunt: it SIGKILLs the wrapper too, so an Agent that used its
    full budget lost the submission it had already written and was scored as a
    zero. A genuinely medal-worthy ML-Master run was recorded as `timed_out,
    score: null` for exactly this reason.

    So the wrapper enforces the budget itself and keeps the remaining window to
    copy the declared artifact out. The Agent is stopped at the same wall clock
    it always was -- this does not extend anyone's solve time -- it only means
    "ran out of time" now publishes whatever the Agent had produced by then,
    instead of discarding it.

    Termination escalates: SIGTERM so the launcher can flush, then SIGKILL if it
    ignores that. Timing out is not an error here; the caller goes on to publish.
    """
    if not argv:
        raise RuntimeError("native wrapper requires a child command after --")
    if budget_seconds is None:
        completed = subprocess.run(argv, check=False)
        if completed.returncode:
            raise RuntimeError(f"native launcher exited with code {completed.returncode}")
        return

    process = subprocess.Popen(argv)
    try:
        return_code = process.wait(timeout=max(1.0, budget_seconds))
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        print(
            f"native launcher reached its {budget_seconds:.0f}s budget; "
            "publishing the artifact it had already written",
            file=sys.stderr,
        )
        return
    if return_code:
        raise RuntimeError(f"native launcher exited with code {return_code}")


def run_ai_scientist(
    output_dir: Path, argv: list[str], *, budget_seconds: float | None = None
) -> None:
    jobs_root = output_dir.resolve() / "jobs"
    before = {path.name for path in jobs_root.iterdir()} if jobs_root.is_dir() else set()
    _run_native(argv, budget_seconds=budget_seconds)
    after = {path.name for path in jobs_root.iterdir() if path.is_dir()} if jobs_root.is_dir() else set()
    new_jobs = sorted(after - before)
    if len(new_jobs) != 1:
        raise RuntimeError(f"expected exactly one new AiScientist job, found {new_jobs}")
    source = jobs_root / new_jobs[0] / "workspace/submission/submission.csv"
    _copy_exclusive(source, output_dir / "submission.csv")


def run_ml_master(
    output_dir: Path,
    workspace_dir: Path,
    argv: list[str],
    *,
    budget_seconds: float | None = None,
) -> None:
    _run_native(argv, budget_seconds=budget_seconds)
    source = workspace_dir.resolve() / "best_submission/submission.csv"
    _copy_exclusive(source, output_dir.resolve() / "submission.csv")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="wrapper", required=True)
    ai_scientist = subparsers.add_parser("ai-scientist")
    ai_scientist.add_argument("--output-dir", type=Path, required=True)
    ai_scientist.add_argument("--budget-seconds", type=float, default=None)
    ai_scientist.add_argument("child", nargs=argparse.REMAINDER)
    ml_master = subparsers.add_parser("ml-master-2")
    ml_master.add_argument("--output-dir", type=Path, required=True)
    ml_master.add_argument("--budget-seconds", type=float, default=None)
    ml_master.add_argument("--workspace-dir", type=Path, required=True)
    ml_master.add_argument("child", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    child = list(args.child)
    if child and child[0] == "--":
        child.pop(0)
    try:
        if args.wrapper == "ai-scientist":
            run_ai_scientist(
                args.output_dir, child, budget_seconds=args.budget_seconds
            )
        else:
            run_ml_master(
                args.output_dir,
                args.workspace_dir,
                child,
                budget_seconds=args.budget_seconds,
            )
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
