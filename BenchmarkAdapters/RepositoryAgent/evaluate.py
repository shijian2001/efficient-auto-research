"""Run a Terminal AO split evaluator in a disposable repository sandbox."""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from ..contracts import AdapterError, require_directory, require_file
from .backend import parse_pass_rate
from .sandbox import RepositorySandbox


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--evaluator", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--python-executable", default="python3")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args(argv)
    repository = require_directory(args.repository, "repository Harness")
    evaluator = require_file(args.evaluator, "Terminal-Bench evaluator")
    data = require_file(args.data, "Terminal-Bench split data")

    with tempfile.TemporaryDirectory(prefix="terminal-ao-evaluation-") as temporary:
        workspace = Path(temporary) / "workspace"
        shutil.copytree(
            repository,
            workspace,
            symlinks=True,
            ignore=shutil.ignore_patterns(".git", ".venv", ".uv-cache", "__pycache__"),
        )
        sandbox = RepositorySandbox(workspace, command_timeout_seconds=args.timeout)
        result = sandbox.run_evaluator(
            python_executable=args.python_executable,
            evaluator=evaluator,
            dev_data=data,
            concurrency=args.concurrency,
        )
    if result.return_code:
        raise AdapterError(
            f"Terminal AO evaluator exited {result.return_code}: {result.output[-4000:]}"
        )
    print(result.output, end="" if result.output.endswith("\n") else "\n")
    print(f"pass_rate: {parse_pass_rate(result.output):.8f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
