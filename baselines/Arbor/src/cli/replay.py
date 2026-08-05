"""Replay a recorded session's ``events.jsonl`` through the live dashboard.

The live dashboard is a pure projection of the event bus (see ``run_state.py``):
every panel — idea tree, score chart, header counters, reasoning feed — is a
function of the events that flow past it. A finished run leaves a complete,
append-only ``events.jsonl``. Replaying it is therefore just: parse each line
back into an event and re-emit it on a fresh bus wired to a fresh dashboard,
pacing the emits by the recorded timestamps.

Why this matters: the live agent needs an LLM key, but *watching a recorded run*
needs nothing. ``arbor replay --demo`` is the zero-setup "see it work" path — the
single biggest onboarding lever — and the same recording doubles as a shareable
artifact.

Design notes:
 - Only bus events drive the dashboard. ``TreeSetMeta`` metadata (baseline /
   metric direction) is not on the bus, so we read it from the sibling
   ``tree.json`` and set the scalar header fields up front; the idea ledger stays
   empty so the tree builds up live as events replay.
 - Gate events (``user.await`` / ``user.input_received``) are skipped — they are
   interactive prompts, not research progress, and a dangling gate panel would
   stick at the end of the replay.
 - Timestamps drive pacing only. Emitted events go through ``bus.emit(type,
   data)``; the dashboard handlers read ``event.data`` and never the timestamp,
   so we don't need to forge the original wall-clock.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..events import types as ev
from ..events.bus import EventBus
from .run_dashboard import RunDashboard
from .run_state import RunState
from .style import console, render_status

# Gate / ask-back events are interactive prompts, not research progress. Replaying
# them would flash (and possibly strand) a gate panel, so we drop them.
_SKIP_TYPES: frozenset[str] = frozenset({ev.AWAIT_USER, ev.USER_INPUT_RECEIVED})

# Default pacing. ``speed`` compresses the original timeline; ``max_gap`` caps any
# single idle stretch so a 20-minute training pause doesn't stall the replay.
DEFAULT_SPEED = 12.0
DEFAULT_MAX_GAP_S = 2.0


@dataclass
class Recording:
    """A parsed ``events.jsonl`` plus the identity/meta we can recover for it."""

    events_path: Path
    events: list[tuple[float, str, dict[str, Any]]]
    session_dir: Path | None = None
    run_name: str = "replay"
    model: str = ""
    task: str = ""
    total_cycles: int | None = None
    metric_direction: str = "maximize"
    baseline_score: float | None = None
    trunk_score: float | None = None
    meta_warnings: list[str] = field(default_factory=list)

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def recorded_duration_s(self) -> float:
        if len(self.events) < 2:
            return 0.0
        return max(0.0, self.events[-1][0] - self.events[0][0])


def resolve_events_path(source: Path) -> Path:
    """Map a user-supplied source to a concrete ``events.jsonl``.

    Accepts either the file itself or a session directory containing it. Raises
    ``FileNotFoundError`` with an actionable message when neither resolves.
    """
    source = Path(source).expanduser()
    if source.is_file():
        return source
    if source.is_dir():
        candidate = source / "events.jsonl"
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(
            f"no events.jsonl in {source} — is this an arbor session directory?"
        )
    raise FileNotFoundError(f"no such file or directory: {source}")


def load_recording(source: Path) -> Recording:
    """Parse a recording from a session dir or an ``events.jsonl`` path."""
    events_path = resolve_events_path(source)
    session_dir = events_path.parent if events_path.name == "events.jsonl" else None
    events = _parse_events(events_path)
    if not events:
        raise ValueError(f"{events_path} contains no replayable events")

    rec = Recording(
        events_path=events_path,
        events=events,
        session_dir=session_dir,
        run_name=(session_dir.name if session_dir else events_path.stem),
    )
    _hydrate_identity(rec)
    if session_dir is not None:
        _hydrate_tree_meta(rec, session_dir / "tree.json")
    return rec


def _parse_events(path: Path) -> list[tuple[float, str, dict[str, Any]]]:
    """Read ``events.jsonl`` into ``(ts, type, data)`` triples, in file order.

    File order is authoritative (it is emission order); malformed or non-event
    lines are skipped rather than aborting the whole replay. A missing/garbled
    timestamp inherits the previous one so pacing degrades to "no delay" instead
    of crashing.
    """
    out: list[tuple[float, str, dict[str, Any]]] = []
    last_ts = 0.0
    with path.open(encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(record, dict):
                continue
            etype = record.get("type")
            if not isinstance(etype, str) or etype in _SKIP_TYPES:
                continue
            data = record.get("data")
            if not isinstance(data, dict):
                data = {}
            ts = record.get("ts")
            ts = float(ts) if isinstance(ts, (int, float)) else last_ts
            last_ts = ts
            out.append((ts, etype, data))
    return out


def _hydrate_identity(rec: Recording) -> None:
    """Recover header identity (model/task/total_cycles) from early events."""
    for ts, etype, data in rec.events:
        if etype == ev.SESSION_START:
            model = data.get("model")
            if isinstance(model, str) and model:
                rec.model = model
            task = data.get("task")
            if isinstance(task, str):
                rec.task = task
        elif etype == ev.CYCLE_START:
            total = data.get("total_cycles")
            if isinstance(total, int):
                rec.total_cycles = total
            break  # identity is all in the opening events; stop early


def _hydrate_tree_meta(rec: Recording, tree_path: Path) -> None:
    """Pull metric direction / baseline / trunk from ``tree.json`` meta.

    These come from ``TreeSetMeta`` (a coordinator tool) which is not on the bus,
    so the events alone can't supply them. We set only the scalar header fields —
    never the node ledger — so the tree still builds up live during replay.
    """
    if not tree_path.is_file():
        rec.meta_warnings.append("no tree.json — baseline/metric direction unavailable")
        return
    try:
        tree = json.loads(tree_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        rec.meta_warnings.append("tree.json unreadable — baseline/metric direction unavailable")
        return
    meta = tree.get("meta") if isinstance(tree, dict) else None
    if not isinstance(meta, dict):
        return
    direction = meta.get("metric_direction")
    if isinstance(direction, str) and direction.lower() in ("maximize", "minimize"):
        rec.metric_direction = direction.lower()
    for attr in ("baseline_score", "trunk_score"):
        value = meta.get(attr)
        if isinstance(value, (int, float)):
            setattr(rec, attr, float(value))


def replay_recording(
    rec: Recording,
    *,
    speed: float = DEFAULT_SPEED,
    max_gap_s: float = DEFAULT_MAX_GAP_S,
) -> str:
    """Drive the live dashboard from a parsed recording. Returns an exit reason.

    Mirrors ``run.py``'s assembly: a fresh ``EventBus`` + ``RunState`` wired into
    a read-only ``RunDashboard``. The main thread paces and emits the recorded
    events while the dashboard's own threads paint. Ctrl-C stops early and leaves
    the final frame on screen.
    """
    speed = max(0.1, float(speed))
    max_gap_s = max(0.0, float(max_gap_s))

    state = RunState(
        run_name=rec.run_name,
        task=rec.task,
        model=rec.model or "recorded",
        total_cycles=rec.total_cycles,
        session_dir=str(rec.session_dir) if rec.session_dir else "",
    )
    # Scalar meta only — leave the ledger empty so the tree grows on screen.
    state.metric_direction = rec.metric_direction
    state.baseline_score = rec.baseline_score
    state.trunk_score = rec.trunk_score

    bus = EventBus()
    exit_reason = "ok"
    # enable_input=False: replay is read-only. The dashboard still renders fully
    # and Ctrl-C still aborts; users just can't steer a recording.
    with RunDashboard(state, bus, enable_input=False):
        state.on_status_narration(
            f"▶ replaying {rec.event_count} events at {speed:g}× — Ctrl-C to stop",
            style="cyan",
            glyph="▶",
        )
        try:
            _drive(bus, rec.events, speed=speed, max_gap_s=max_gap_s)
            state.on_status_narration(
                "■ replay complete — recorded run finished",
                style="green",
                glyph="■",
            )
            # Hold the final frame briefly so the last paint lands before teardown.
            time.sleep(0.6)
        except KeyboardInterrupt:
            exit_reason = "interrupted"
    return exit_reason


def _drive(
    bus: EventBus,
    events: list[tuple[float, str, dict[str, Any]]],
    *,
    speed: float,
    max_gap_s: float,
) -> None:
    """Emit each event after sleeping the (scaled, clamped) inter-event delay."""
    prev_ts: float | None = None
    for ts, etype, data in events:
        if prev_ts is not None:
            delay = (ts - prev_ts) / speed
            if delay > 0:
                time.sleep(min(delay, max_gap_s))
        prev_ts = ts
        bus.emit(etype, data)


# ── bundled demo ───────────────────────────────────────────────────────────
# Ships as package data (see pyproject [tool.setuptools.package-data]). Resolving
# relative to this module works both in an editable/source checkout and in a
# built wheel.

DEMO_DIR = Path(__file__).resolve().parent / "assets" / "demo_session"


def demo_recording() -> Recording:
    """Load the bundled zero-API demo recording."""
    if not (DEMO_DIR / "events.jsonl").is_file():
        raise FileNotFoundError(
            "bundled demo recording is missing — expected "
            f"{DEMO_DIR / 'events.jsonl'}"
        )
    rec = load_recording(DEMO_DIR)
    rec.run_name = "demo"
    # The recording lives inside the package; don't let `--html` default to
    # writing artifacts back into it. A demo export lands in the cwd instead.
    rec.session_dir = None
    return rec


def print_recording_banner(rec: Recording, *, is_demo: bool) -> None:
    """One-line context above the dashboard so the user knows what they're seeing."""
    label = "bundled sample session" if is_demo else "recorded session"
    dur = rec.recorded_duration_s
    dur_text = f"{dur / 60:.0f}m of real run" if dur >= 60 else f"{dur:.0f}s of real run"
    console.print(
        f"[bold yellow]arbor replay[/] [dim]·[/] {label} "
        f"[dim]·[/] {rec.event_count} events [dim]·[/] {dur_text}"
    )
    if is_demo:
        console.print(
            "[dim]illustrative sample — replays the dashboard with no API key. "
            "Run a real session with [/][cyan]arbor[/][dim] (needs a model).[/]"
        )
    for warning in rec.meta_warnings:
        render_status(warning, style="yellow", glyph="!")
