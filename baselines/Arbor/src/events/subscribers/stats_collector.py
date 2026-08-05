"""Aggregate event counts during a session.

Subscribes to the EventBus and keeps a single in-memory dict of counters
that the report generator can ask for at session end. Cheap — no I/O,
no allocation per event beyond a dict update.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..bus import Event, EventBus
from .. import types as ev


@dataclass
class EventStats:
    counts: Counter[str] = field(default_factory=Counter)
    llm_errors: int = 0
    eval_failures: int = 0
    ideas_proposed: int = 0
    ideas_completed: int = 0
    ideas_pruned: int = 0
    ideas_merged: int = 0
    executor_runs: int = 0
    cycles: int = 0
    session_start_ts: float | None = None
    session_end_ts: float | None = None
    session_payload: dict[str, Any] = field(default_factory=dict)
    end_payload: dict[str, Any] = field(default_factory=dict)

    @property
    def session_duration_s(self) -> float | None:
        if self.session_start_ts is None or self.session_end_ts is None:
            return None
        return max(0.0, self.session_end_ts - self.session_start_ts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "counts": dict(self.counts),
            "llm_errors": self.llm_errors,
            "eval_failures": self.eval_failures,
            "ideas_proposed": self.ideas_proposed,
            "ideas_completed": self.ideas_completed,
            "ideas_pruned": self.ideas_pruned,
            "ideas_merged": self.ideas_merged,
            "executor_runs": self.executor_runs,
            "cycles": self.cycles,
            "session_duration_s": self.session_duration_s,
            "session_payload": self.session_payload,
            "end_payload": self.end_payload,
        }


class StatsCollector:
    def __init__(self) -> None:
        self.stats = EventStats()

    def attach(self, bus: EventBus) -> None:
        bus.on_all(self._on_any)
        bus.on(ev.SESSION_START, self._on_session_start)
        bus.on(ev.SESSION_END, self._on_session_end)
        bus.on(ev.IDEA_PROPOSED, self._inc_proposed)
        bus.on(ev.IDEA_COMPLETED, self._inc_completed)
        bus.on(ev.IDEA_PRUNED, self._inc_pruned)
        bus.on(ev.IDEA_MERGED, self._inc_merged)
        bus.on(ev.EXECUTOR_END, self._inc_executor)
        bus.on(ev.CYCLE_END, self._inc_cycle)
        bus.on(ev.LLM_ERROR, self._inc_llm_error)
        bus.on(ev.EVAL_END, self._maybe_inc_eval_failure)

    # ── handlers ───────────────────────────────────────────────

    def _on_any(self, e: Event) -> None:
        self.stats.counts[e.type] += 1

    def _on_session_start(self, e: Event) -> None:
        self.stats.session_start_ts = e.timestamp
        self.stats.session_payload = dict(e.data)

    def _on_session_end(self, e: Event) -> None:
        self.stats.session_end_ts = e.timestamp
        self.stats.end_payload = dict(e.data)

    def _inc_proposed(self, _e: Event) -> None:
        self.stats.ideas_proposed += 1

    def _inc_completed(self, _e: Event) -> None:
        self.stats.ideas_completed += 1

    def _inc_pruned(self, _e: Event) -> None:
        self.stats.ideas_pruned += 1

    def _inc_merged(self, _e: Event) -> None:
        self.stats.ideas_merged += 1

    def _inc_executor(self, _e: Event) -> None:
        self.stats.executor_runs += 1

    def _inc_cycle(self, _e: Event) -> None:
        self.stats.cycles += 1

    def _inc_llm_error(self, _e: Event) -> None:
        self.stats.llm_errors += 1

    def _maybe_inc_eval_failure(self, e: Event) -> None:
        # Heuristic: an EVAL_END with no/zero score and an explicit error flag.
        if e.data.get("error") or e.data.get("failed"):
            self.stats.eval_failures += 1
