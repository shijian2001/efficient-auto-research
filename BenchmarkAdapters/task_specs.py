"""Canonical task specifications shared verbatim by all Agent launchers."""

from __future__ import annotations

from pathlib import Path

from .contracts import AdapterError
from .protocol import sha256_file


TASK_SPEC_ROOT = Path(__file__).with_name("task_specs")
TASK_SPEC_FILES = {
    "mle-bench-lite": "mle-bench-lite.md",
    "terminal-bench-ao": "terminal-bench-ao.md",
    "autoresearch-architecture": "autoresearch-architecture.md",
    "optimizer-design": "optimizer-design.md",
    "fml-bench": "fml-bench.md",
    # Addendum, not a task specification: it carries no task content and only
    # tells a single-invocation CLI harness what the frozen spec leaves to an
    # Agent's own loop -- that nothing restarts it, and that the budget is a
    # target rather than a ceiling. Agents that already run their own search
    # loop do not read it, so the shared spec above stays byte-identical for
    # every Agent and the protocol's task_spec digest does not move.
    "mle-bench-lite-cli-harness": "mle-bench-lite.cli-harness-addendum.md",
}


def task_spec_path(benchmark_id: str) -> Path:
    try:
        path = TASK_SPEC_ROOT / TASK_SPEC_FILES[benchmark_id]
    except KeyError as exc:
        raise AdapterError(f"unknown canonical benchmark task specification: {benchmark_id}") from exc
    if not path.is_file() or path.is_symlink():
        raise AdapterError(f"canonical task specification is missing: {path}")
    return path.resolve()


def task_spec_digest(benchmark_id: str) -> str:
    return sha256_file(task_spec_path(benchmark_id))


def task_spec_text(benchmark_id: str) -> str:
    return task_spec_path(benchmark_id).read_text(encoding="utf-8")


__all__ = ["TASK_SPEC_FILES", "TASK_SPEC_ROOT", "task_spec_digest", "task_spec_path", "task_spec_text"]
