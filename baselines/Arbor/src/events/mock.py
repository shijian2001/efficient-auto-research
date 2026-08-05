"""Mock event generator — a scripted run for offline UI development (B1.3).

Lets the observability surfaces (tree viz #6, WebUI #7) be built and demoed
*before* the engine emits real events. It replays a representative sequence
onto any ``EventBus``/``NullBus`` using the exact constants and payload shapes
frozen in :mod:`events.types` / :mod:`events.payloads`.

Usage::

    from arbor.events import EventBus
    from arbor.events.mock import emit_mock_run

    bus = EventBus()
    # ... attach your subscribers ...
    emit_mock_run(bus)              # fire instantly
    emit_mock_run(bus, delay=0.2)   # paced, for a live-looking demo
"""

from __future__ import annotations

import time
from typing import Any

from . import types as E
from .bus import EventBus, NullBus

#: A deterministic, ordered script of (event_type, payload) pairs covering the
#: full lifecycle plus every contract-2 event. Kept JSON-serializable.
MOCK_SCRIPT: list[tuple[str, dict[str, Any]]] = [
    (E.SESSION_START, {"task": "Improve validation accuracy", "cwd": "/repo", "provider": "claude", "model": "claude-sonnet-4-6"}),
    (E.CYCLE_START, {"cycle_num": 1, "total_cycles": 3}),
    (E.PHASE_CHANGE, {"phase": "ideate"}),
    (E.THINKING_DELTA, {"node_id": "", "text": "Two angles look promising: ", "agent": "coordinator"}),
    (E.THINKING_DELTA, {"node_id": "", "text": "stronger augmentation and a cosine LR schedule.", "agent": "coordinator"}),
    (E.IDEA_PROPOSED, {"node_id": "n1", "hypothesis": "Add RandAugment", "parent_id": None}),
    (E.AWAIT_USER, {"kind": "idea_review", "prompt": "Run idea n1?", "node_id": "n1", "options": ["approve", "skip", "edit"]}),
    (E.USER_INPUT_RECEIVED, {"node_id": "n1", "value": "approve"}),
    (E.EXECUTOR_START, {"node_id": "n1", "idea": "Add RandAugment", "branch": "research/run/n1"}),
    (E.TOOL_START, {"name": "Bash", "args_preview": "python train.py --aug randaugment", "agent": "sub:n1", "node_id": "n1"}),
    (E.CACHE_STAT, {"cache_read": 18000, "cache_write": 2000, "miss": 1500, "total": 21500}),
    (E.HEARTBEAT, {"agent": "sub:n1", "node_id": "n1", "operation": "tool:Bash", "elapsed_seconds": 300.0, "detail": "Bash"}),
    (E.TOOL_END, {"name": "Bash", "ok": True, "duration": 742.0, "output_preview": "val_acc=0.913", "agent": "sub:n1", "node_id": "n1"}),
    (E.EXECUTOR_END, {"node_id": "n1", "score": 0.913, "duration": 760.0, "tokens": 41000}),
    (E.TREE_UPDATED, {"tree_snapshot_path": ".coordinator/idea_tree.json"}),
    (E.CHECKPOINT_SAVED, {"path": ".arbor/checkpoint/checkpoint.json", "cycle": 1, "reason": "cycle_end"}),
    (E.CYCLE_END, {"cycle_num": 1, "duration": 805.0}),
    (E.SESSION_END, {"duration": 810.0, "exit_reason": "ok", "turns": 12, "input_tokens": 120000, "output_tokens": 8000, "meta_input_tokens": 60000, "meta_output_tokens": 4000}),
]


def emit_mock_run(bus: EventBus | NullBus, *, delay: float = 0.0) -> None:
    """Replay :data:`MOCK_SCRIPT` onto ``bus``.

    ``delay`` (seconds) is slept between events for a live-looking demo; the
    default of 0 fires the whole script instantly (use this in tests).
    """
    for event_type, data in MOCK_SCRIPT:
        bus.emit(event_type, data)
        if delay:
            time.sleep(delay)
