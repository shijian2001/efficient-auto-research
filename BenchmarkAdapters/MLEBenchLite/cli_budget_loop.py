"""Keep a single-invocation CLI Agent working for its whole wall-clock budget.

Codex and Claude Code are shaped as assistants: they take a task, do it, and
return. Returning ends the cell, so a twelve-hour budget bought roughly half an
hour of work. Telling them about the budget helped -- the budget briefing raised
Codex from 388s to 1905s and turned an invalid mlsp cell into a silver medal --
but it could not remove the state the architecture has and a search loop does
not: "done". The Agent signs off with a delivery summary because, on its own
terms, it finished.

So the loop lives outside the Agent. Each pass resumes the same session and asks
it to keep going while budget remains; the CLI itself is unmodified, and every
decision about what to try next stays with the Agent. The continuation prompt is
deliberately free of technique -- no ensembling, tuning, or model suggestions --
because a hint here would make the score partly ours rather than the Agent's.

This is a harness-supplied loop, and the Agents it is compared against supply
their own. That difference does not disappear because the wall-clock now
matches, so it is recorded rather than smoothed over: the cell runs under an
explicit `*-budget-loop` variant, which lands in the manifest, and every pass is
journalled to `budget-loop.jsonl` so the number of turns and the submissions
they produced can be audited after the fact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path


# Seconds held back from the last pass so the Agent's final submission is on
# disk before the outer harness stops the cell, mirroring the reservation
# native_wrappers makes for the same reason.
_PUBLISH_GRACE_SECONDS = 90.0

# A pass shorter than this cannot produce useful work, so the loop stops instead
# of spending the remainder on process startup.
_MINIMUM_PASS_SECONDS = 120.0

# Seconds a pass gets to exit on SIGTERM before it is killed.
_TERMINATE_GRACE_SECONDS = 20.0

# Consecutive non-zero exits treated as the CLI being unable to continue. One
# failure can be a transient upstream error; several in a row means the loop
# would otherwise spin without doing work.
_MAX_CONSECUTIVE_FAILURES = 3


def _submission_digest(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def continuation_prompt(remaining_seconds: float) -> str:
    """What the Agent is told when it returns with budget left.

    States the clock and the standing requirement, and nothing else. Naming a
    technique here -- try ensembling, widen the search, use a bigger model --
    would put our judgement inside the Agent's result, so the prompt says only
    that time remains and that the submission may be replaced when the Agent's
    own measurement says it improved.
    """
    hours = remaining_seconds / 3600
    if hours >= 1:
        remaining = f"{hours:.1f} hours"
    else:
        remaining = f"{max(1, int(remaining_seconds // 60))} minutes"
    return (
        f"You have {remaining} ({int(remaining_seconds)} seconds) of your budget left, "
        "and this session is still running. The task is unchanged and your earlier "
        "work is intact.\n\n"
        "Keep improving your solution. Measure each change against your own "
        "validation split, and overwrite submission.csv only when your own "
        "measurement says the new approach is better -- a worse result must not "
        "replace a better one already on disk.\n\n"
        "Do not stop because a submission already exists or because the last "
        "result looked reasonable. Stop only if your own validation has not "
        "improved across several consecutive substantive attempts."
    )


def _run_pass(argv: list[str], *, timeout: float) -> tuple[int | None, bool]:
    """Run one pass, returning (exit code, whether the budget stopped it)."""
    process = subprocess.Popen(argv)
    try:
        return process.wait(timeout=max(1.0, timeout)), False
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=_TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        return None, True


def run_budget_loop(
    *,
    first_argv: list[str],
    resume_argv: list[str],
    budget_seconds: float,
    submission_path: Path,
    journal_path: Path | None = None,
    now=time.monotonic,
) -> int:
    """Drive the CLI until the budget is spent, journalling every pass."""
    if not first_argv or not resume_argv:
        raise RuntimeError("budget loop needs both a first and a resume command")
    deadline = now() + budget_seconds
    journal_path = journal_path.resolve() if journal_path is not None else None
    if journal_path is not None:
        journal_path.parent.mkdir(parents=True, exist_ok=True)

    passes = 0
    failures = 0
    while True:
        remaining = deadline - now() - _PUBLISH_GRACE_SECONDS
        if remaining < _MINIMUM_PASS_SECONDS:
            break
        if passes == 0:
            argv = list(first_argv)
        else:
            argv = [*resume_argv, continuation_prompt(remaining)]

        started_at = time.time()
        started = now()
        return_code, stopped_by_budget = _run_pass(argv, timeout=remaining)
        passes += 1

        if journal_path is not None:
            record = {
                "pass": passes,
                "started_at": started_at,
                "duration_seconds": round(now() - started, 3),
                "remaining_seconds_at_start": round(remaining, 3),
                "return_code": return_code,
                "stopped_by_budget": stopped_by_budget,
                "submission_sha256": _submission_digest(submission_path),
            }
            with journal_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")

        if stopped_by_budget:
            print(
                f"budget loop: pass {passes} reached the deadline; "
                "keeping whatever submission is on disk",
                file=sys.stderr,
            )
            break
        if return_code:
            failures += 1
            if failures >= _MAX_CONSECUTIVE_FAILURES:
                print(
                    f"budget loop: {failures} consecutive failing passes; stopping "
                    f"with {max(0.0, deadline - now()):.0f}s unused",
                    file=sys.stderr,
                )
                break
        else:
            failures = 0

    print(f"budget loop: {passes} pass(es) completed", file=sys.stderr)
    # A loop that ran at all is not itself a failure: the cell is judged on the
    # submission, and the adapter's own checks decide whether one exists.
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget-seconds", type=float, required=True)
    parser.add_argument("--submission", type=Path, required=True)
    parser.add_argument("--journal", type=Path)
    parser.add_argument(
        "--resume-command",
        required=True,
        help="JSON list: the argv that resumes the session, minus the prompt",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    first = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not first:
        parser.error("provide the Agent's first command after --")
    resume = json.loads(args.resume_command)
    if not isinstance(resume, list) or not all(isinstance(item, str) for item in resume):
        parser.error("--resume-command must be a JSON list of strings")
    return run_budget_loop(
        first_argv=list(first),
        resume_argv=resume,
        budget_seconds=args.budget_seconds,
        submission_path=args.submission,
        journal_path=args.journal,
    )


if __name__ == "__main__":
    raise SystemExit(main())
