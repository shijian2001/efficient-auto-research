"""Startup checkpoint picker.

Each `arbor` run lives in its own session dir at
``<cwd>/.arbor/sessions/<run_name>/`` with a checkpoint at
``.coordinator/checkpoint.json``. When the user launches `arbor` in a project that
already has past sessions, we list them and let them choose: start fresh, or
resume one — instead of silently ignoring them (you'd otherwise have to know and
pass ``--run-name <x> --resume`` by hand).

Read-only and defensive: a malformed or newer-schema checkpoint is skipped, never
fatal. Selecting a session is exactly equivalent to passing
``--run-name <x> --resume`` — the engine's resume path is reused unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .._app import CONFIG_DIR_NAME
from ..coordinator.checkpoint import read_checkpoint


@dataclass
class ResumableSession:
    """One past run the user could resume, summarized for the picker."""

    run_name: str
    session_dir: Path
    cwd: Path
    created_at: datetime | None
    cycle_num: int
    phase: str
    best_score: float | None
    total_nodes: int | None
    completed: bool
    task: str


def find_resumable_sessions(cwd: Path, *, include_subdirs: bool = False) -> list[ResumableSession]:
    """Return resumable sessions under ``<cwd>/.arbor/sessions``, newest first.

    A session is resumable iff it has a readable ``.coordinator/checkpoint.json``.
    Completed runs (those that wrote ``run_stats.json``) are included too — they
    can be resumed to push further — and tagged ``completed``.

    When ``include_subdirs`` is set and the launch dir itself has no sessions,
    we scan one level of immediate subdirectories too. Runs live under the
    *target project* dir (which intake may have redirected to), so a user who
    launched ``arbor`` from a parent folder would otherwise see nothing.
    """
    sessions = _scan_dir(Path(cwd))
    if not sessions and include_subdirs:
        for child in _safe_iterdir(Path(cwd)):
            if child.is_dir() and not child.name.startswith("."):
                sessions.extend(_scan_dir(child))

    # Newest first. Sessions without a parseable timestamp sort last.
    sessions.sort(
        key=lambda s: (s.created_at is not None, s.created_at or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )
    return sessions


def _safe_iterdir(path: Path) -> list[Path]:
    try:
        return sorted(path.iterdir())
    except OSError:
        return []


def _scan_dir(cwd: Path) -> list[ResumableSession]:
    """Collect resumable sessions directly under ``<cwd>/.arbor/sessions`` (unsorted)."""
    base = cwd / CONFIG_DIR_NAME / "sessions"
    if not base.is_dir():
        return []

    sessions: list[ResumableSession] = []
    for session_dir in base.iterdir():
        if not session_dir.is_dir():
            continue
        ckpt_path = session_dir / ".coordinator" / "checkpoint.json"
        if not ckpt_path.is_file():
            continue
        try:
            ckpt = read_checkpoint(ckpt_path)
        except Exception:
            ckpt = None              # unreadable / newer schema → skip silently
        if ckpt is None:
            continue

        stats = _load_json(session_dir / "run_stats.json")
        completed = stats is not None
        best_score = total_nodes = None
        if isinstance(stats, dict):
            iters = stats.get("iterations") or {}
            best_score = iters.get("best_score")
            total_nodes = iters.get("total_nodes")

        start = _session_start(session_dir / "events.jsonl")
        task = (start.get("task") if start else None) or "(no task recorded)"
        cwd_from_log = start.get("cwd") if start else None
        resolved_cwd = (
            Path(cwd_from_log) if cwd_from_log
            else _first_worktree(ckpt) or Path(cwd)
        )

        sessions.append(ResumableSession(
            run_name=ckpt.run_name or session_dir.name,
            session_dir=session_dir,
            cwd=resolved_cwd,
            created_at=_parse_iso(ckpt.created_at),
            cycle_num=ckpt.cycle_num,
            phase=ckpt.phase,
            best_score=best_score if isinstance(best_score, (int, float)) else None,
            total_nodes=total_nodes if isinstance(total_nodes, int) else None,
            completed=completed,
            task=task,
        ))

    return sessions


def prompt_resume_choice(sessions: list[ResumableSession], *, console: Any) -> ResumableSession | None:
    """Show the sessions and ask. Return the chosen session, or None for a new run."""
    import typer

    console.print()
    console.print(f"[bold cyan]Found {len(sessions)} previous run(s)[/] you can resume:\n")
    for i, s in enumerate(sessions, 1):
        console.print(_format_row(i, s))
    console.print()

    while True:
        answer = typer.prompt(
            f"Start a new run, or resume one? [N / 1-{len(sessions)}]",
            default="N",
        ).strip().lower()
        if answer in ("", "n", "new"):
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(sessions):
            return sessions[int(answer) - 1]
        console.print(f"[yellow]  enter N for a new run, or 1-{len(sessions)} to resume[/]")


# ── helpers ──────────────────────────────────────────────────────────


def _format_row(idx: int, s: ResumableSession) -> str:
    from rich.markup import escape

    age = _humanize_age(s.created_at)
    # Escape the leading bracket so Rich renders a literal "[completed]" tag
    # rather than parsing it as a (bogus) style tag and swallowing it.
    tag = "[green]\\[completed][/]" if s.completed else "[yellow]\\[interrupted][/]"
    best = f"best {s.best_score:.4f}" if s.best_score is not None else "best —"
    nodes = f"{s.total_nodes} idea(s)" if s.total_nodes is not None else "—"
    task = escape(_short(s.task, 60))
    project = escape(_short(str(s.cwd), 70))
    return (
        f"  [bold]{idx}[/]  [cyan]{escape(s.run_name)}[/]  [dim]{age}[/]  "
        f"{escape(s.phase)} · cycle {s.cycle_num}  [dim]{best} · {nodes}[/]  {tag}\n"
        f"      [dim]{project}[/]\n"
        f"      [dim]{task}[/]"
    )


def _humanize_age(dt: datetime | None) -> str:
    if dt is None:
        return "unknown time"
    now = datetime.now(timezone.utc)
    secs = max(0, int((now - dt).total_seconds()))
    if secs < 90:
        return "just now"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _load_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _session_start(events_path: Path) -> dict[str, Any] | None:
    """Return the payload of the first ``session.start`` event, if any."""
    try:
        with events_path.open(encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("type") == "session.start":
                    return rec.get("data") or {}
                return None          # session.start is always first; bail early
    except (OSError, ValueError):
        return None
    return None


def _first_worktree(ckpt: Any) -> Path | None:
    try:
        wts = ckpt.git.worktrees
    except AttributeError:
        return None
    return Path(wts[0]) if wts else None


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
