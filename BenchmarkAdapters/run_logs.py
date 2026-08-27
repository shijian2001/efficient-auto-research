"""A browsable index of every cell's logs, laid out by benchmark/agent/task.

Run directories are organised for the harness: campaign dir, then agent, then
seed, then task, with each Agent's own logs buried at a different depth per
adapter path. That is fine for machines and useless for a person asking "what
did Arbor do on jigsaw last Tuesday". This module maintains a second view of the
same files -- symlinks, never copies -- under a single root:

    run-logs/index/
      mle-bench-lite/<agent>/<task>/<run-id>/
      terminal-ao/<agent>/<task>/<run-id>/

Each run directory links the cell's own artifacts (result.json, manifest.json,
agent.log, ...) plus a `cell` link to the whole run directory, so anything not
enumerated here is still one hop away. Symlinks mean the index costs nothing and
can never disagree with the real evidence; if a campaign directory is deleted the
dangling links make that obvious rather than leaving a stale copy that looks real.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .registry import ROOT

INDEX_ROOT = ROOT / "run-logs" / "index"

# Files worth surfacing directly at the top of a run's index entry. Anything else
# stays reachable through the `cell` link.
_LINKED_ARTIFACTS = (
    "result.json",
    "manifest.json",
    "agent.log",
    "agent-output/token_usage.jsonl",
    "agent-output/relay.log",
    "grading/competition_report.json",
)


def _relative_symlink(link: Path, target: Path) -> None:
    """Point `link` at `target` using a relative path, replacing any old link.

    Relative targets keep the index valid if the repository is moved or mounted
    elsewhere. Only symlinks are ever replaced -- a real file at that path is a
    sign something else owns it, so leave it alone rather than delete data.
    """
    if link.is_symlink():
        link.unlink()
    elif link.exists():
        return
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(os.path.relpath(target.resolve(), link.parent.resolve()))


def index_run(
    *,
    benchmark_id: str,
    agent: str,
    task_id: str,
    run_id: str,
    run_dir: Path,
) -> Path:
    """Publish one cell into the browsable index. Never raises into a run.

    Indexing is bookkeeping: a failure here must not turn a completed cell into a
    failed one, so every error is swallowed. The authoritative evidence is the run
    directory itself, which is written before this is ever called.
    """
    try:
        run_dir = run_dir.resolve()
        entry = INDEX_ROOT / benchmark_id / agent / task_id / run_id
        entry.mkdir(parents=True, exist_ok=True)
        _relative_symlink(entry / "cell", run_dir)
        for relative in _LINKED_ARTIFACTS:
            source = run_dir / relative
            if source.exists():
                _relative_symlink(entry / Path(relative).name, source)
        _write_summary(entry, run_dir, benchmark_id, agent, task_id, run_id)
        return entry
    except OSError:
        return INDEX_ROOT


def _write_summary(
    entry: Path, run_dir: Path, benchmark_id: str, agent: str, task_id: str, run_id: str
) -> None:
    """A one-glance summary so the index answers the common question without a hop."""
    summary: dict[str, object] = {
        "benchmark": benchmark_id,
        "agent": agent,
        "task": task_id,
        "run_id": run_id,
        "cell_dir": str(run_dir),
    }
    result_path = run_dir / "result.json"
    if result_path.is_file():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            result = {}
        for field in ("status", "score", "score_valid", "failure_reason",
                      "wall_clock_seconds", "tokens"):
            if field in result:
                summary[field] = result[field]
    logs = []
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if path.suffix in {".log", ".jsonl"} or path.name.endswith(".log"):
            try:
                logs.append({"path": str(path.relative_to(run_dir)), "bytes": path.stat().st_size})
            except OSError:
                continue
    summary["logs"] = sorted(logs, key=lambda item: -int(item["bytes"]))[:40]
    (entry / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


__all__ = ["INDEX_ROOT", "index_run"]
