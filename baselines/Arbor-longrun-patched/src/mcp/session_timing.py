"""Lightweight timing/activity sidecar for keyless Arbor sessions.

The keyless MCP path (:mod:`arbor.mcp.session_ops`) persists the Idea Tree, but
the :class:`~arbor.coordinator.idea_tree.Node` model intentionally carries **no
timestamps** — so a file-backed WebUI has no clock, no per-node runtime, and no
"recent activity" to render. Rather than pollute the byte-compatible tree JSON,
this module maintains a *separate* sidecar (``.coordinator/activity.json``) that
records when nodes were proposed / started / finished, plus a capped, append-only
event log. The WebUI's :mod:`arbor.webui.session_source` reads it to fill
``elapsed_seconds``, per-node ``runtime_seconds``/``finished_elapsed``, the active
pipeline phase, and the recent-activity feed.

It is best-effort telemetry: every write is wrapped so a sidecar failure can
never break the real tree mutation that triggered it.

It assumes a *single writer* per session — the keyless MCP path is single-agent —
so the read-modify-write in :func:`record_event` is not locked; truly concurrent
writers could drop one another's events. The atomic rename in :func:`_save` (write
to a per-writer temp file, then ``replace``) still guarantees a reader never sees a
half-written file even under concurrency.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

SIDECAR_NAME = "activity.json"

# Cap the event log so a long run can't grow the sidecar unbounded; the WebUI
# only ever shows the most recent handful.
_MAX_EVENTS = 200

# Kinds that mark a node as having started running / finished — used to stamp the
# per-node started_at / finished_at timing.
_RUNNING_KINDS = {"running"}
_TERMINAL_KINDS = {"done", "merged", "pruned", "needs_retry", "failed"}


def _path(coord_dir: str | Path) -> Path:
    return Path(coord_dir) / SIDECAR_NAME


def load(coord_dir: str | Path) -> dict[str, Any]:
    """Load the sidecar, returning an empty shape on any missing/invalid file."""
    try:
        data = json.loads(_path(coord_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(coord_dir: str | Path, data: dict[str, Any]) -> None:
    path = _path(coord_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A per-writer temp name (pid + thread) so two concurrent _save calls can't
    # clobber a shared temp file mid-write; the rename onto the final path stays
    # atomic. (record_event's read-modify-write still assumes a single writer.)
    tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)  # don't leave a stray temp file behind
        raise


def record_event(
    coord_dir: str | Path,
    node_id: str | None,
    kind: str,
    *,
    label: str | None = None,
    score: float | None = None,
) -> None:
    """Append an activity event and stamp node timing. Never raises.

    ``kind`` is a coarse lifecycle label (``proposed``/``running``/``done``/
    ``merged``/``pruned``/``needs_retry``/``failed``). The first event ever
    recorded sets ``session_started_at`` (the WebUI's run clock). ``running``
    stamps the node's ``started_at``; terminal kinds stamp ``finished_at``.
    """
    try:
        now = time.time()
        data = load(coord_dir)
        data.setdefault("session_started_at", now)
        data["updated_at"] = now

        if node_id:
            nodes = data.setdefault("nodes", {})
            timing = nodes.setdefault(node_id, {})
            timing.setdefault("created_at", now)
            if kind in _RUNNING_KINDS:
                # A retry re-enters "running": refresh started_at and clear the
                # previous finish so runtime reflects the active attempt.
                timing["started_at"] = now
                timing.pop("finished_at", None)
            elif kind in _TERMINAL_KINDS:
                timing.setdefault("started_at", timing.get("created_at", now))
                timing["finished_at"] = now

        event: dict[str, Any] = {"ts": now, "kind": kind}
        if node_id:
            event["node_id"] = node_id
        if label:
            event["label"] = label
        if score is not None:
            event["score"] = score
        events = data.setdefault("events", [])
        events.append(event)
        if len(events) > _MAX_EVENTS:
            del events[: len(events) - _MAX_EVENTS]

        _save(coord_dir, data)
    except Exception:  # pragma: no cover - telemetry must never break a mutation
        return
