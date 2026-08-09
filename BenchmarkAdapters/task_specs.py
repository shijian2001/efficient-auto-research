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
