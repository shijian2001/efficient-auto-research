"""Serialize the live ``RunState`` into a JSON-safe dict for the WebUI (#7).

The WebUI consumes the same state the terminal dashboard renders; this is the
single place that flattens it for the browser. Pure and JSON-native — never
includes secrets (RunState holds none; model/cwd are safe). Monotonic clocks are
converted to elapsed seconds so the browser gets meaningful numbers.
"""

from __future__ import annotations

import time
from typing import Any


def empty_state_dict() -> dict[str, Any]:
    """Return the minimal WebUI snapshot shape expected by the browser."""
    return {
        "run_name": "run",
        "task": "",
        "cwd": "",
        "model": "—",
        "phase": "connecting",
        "cycle_num": 0,
        "total_cycles": 0,
        "branch_budget_used": 0,
        "elapsed_seconds": 0,
        "counters": {
            "proposed": 0,
            "done": 0,
            "pruned": 0,
            "merged": 0,
            "running": 0,
        },
        "best_score": None,
        "baseline_score": None,
        "metric_direction": "maximize",
        "best_score_history": [],
        "tokens": {"input": 0, "output": 0},
        "cache": {"read": 0, "creation": 0, "uncached": 0, "hit_rate": 0},
        "tree": [],
        "thinking": [],
        "agents": {},
        "idle_seconds": None,
        # Interactive surfaces (filled by state_to_dict / the server).
        "companion": {"turns": [], "busy": False},
        "gate": None,
        "paused": False,
        "interactive": False,
        # True only for the file-backed (keyless) monitor, where token/cache
        # accounting is owned by the host harness and not observable by Arbor —
        # the browser uses this to hide those panels rather than show 0s.
        "keyless": False,
    }


def _gate_to_dict(gate: Any) -> dict[str, Any] | None:
    """Whitelist the pending-gate fields the browser needs (and keep it
    JSON-safe — never forward arbitrary event payload objects)."""
    if not gate:
        return None
    return {
        "kind": str(gate.get("kind") or "review"),
        "prompt": str(gate.get("prompt") or ""),
        "node_id": str(gate.get("node_id") or ""),
        "options": [str(o) for o in (gate.get("options") or [])],
    }


def _idea_to_dict(rec: Any, now: float, run_started: float) -> dict[str, Any]:
    runtime = None
    if rec.started_at is not None:
        end = rec.finished_at if rec.finished_at is not None else now
        runtime = round(end - rec.started_at, 1)
    # Absolute elapsed (since run start) at which this idea finished — the x
    # coordinate for the WebUI score-over-time scatter. None while in flight.
    finished_elapsed = (
        round(rec.finished_at - run_started, 1)
        if rec.finished_at is not None else None
    )
    return {
        "node_id": rec.node_id,
        "hypothesis": rec.hypothesis,
        "status": rec.status,
        "score": rec.score,
        "score_split": getattr(rec, "score_split", "dev"),
        "test_score": getattr(rec, "test_score", None),
        "branch": rec.branch,
        "parent_id": rec.parent_id,
        "runtime_seconds": runtime,
        "finished_elapsed": finished_elapsed,
        "pruned_reason": getattr(rec, "pruned_reason", None),
        "insight": getattr(rec, "insight", None),
    }


def state_to_dict(s: Any) -> dict[str, Any]:
    """Flatten ``RunState`` for the WebUI. Safe to call from any thread."""
    now = time.monotonic()
    # Copy the ledger defensively — the event thread mutates it concurrently.
    try:
        order = list(s.idea_order)
        ideas = dict(s.ideas)
    except RuntimeError:
        with s._lock:
            order = list(s.idea_order)
            ideas = dict(s.ideas)

    tree = [_idea_to_dict(ideas[n], now, s.started_at) for n in order if n in ideas]

    # Companion conversation (browser chat surface). Defensive copy of the deque.
    try:
        turns = list(s.companion_turns)
    except (RuntimeError, AttributeError):
        turns = []
    companion = {
        "turns": [[role, text] for role, text in turns],
        "busy": bool(getattr(s, "companion_busy", False)),
    }

    agents: dict[str, Any] = {}
    try:
        activity_items = list(s.agent_activity.items())
    except RuntimeError:        # event thread added an agent mid-iteration
        activity_items = []
    for label, act in activity_items:
        started = act.get("started_at")
        running = act.get("ok") is None
        agents[label] = {
            "tool": act.get("tool"),
            "node_id": act.get("node_id"),
            "preview": act.get("preview"),
            "ok": act.get("ok"),
            "elapsed": round(now - started, 1) if (running and started) else None,
            "duration": act.get("duration"),
        }

    return {
        "run_name": s.run_name,
        "task": s.task,
        "cwd": s.cwd,
        "model": s.model,
        "phase": s.phase,
        "cycle_num": s.cycle_num,
        "total_cycles": s.total_cycles,
        "branch_budget_used": s.branch_budget_used,
        "elapsed_seconds": round(s.elapsed_seconds, 1),
        "counters": {
            "proposed": s.ideas_proposed,
            "done": s.ideas_done,
            "pruned": s.ideas_pruned,
            "merged": s.ideas_merged,
            "running": s.ideas_running,
            "needs_retry": s.ideas_needs_retry,
        },
        "best_score": s.best_score,
        "baseline_score": s.baseline_score,
        "metric_direction": s.metric_direction,
        "best_score_history": list(s.best_score_history),
        "tokens": {"input": s.tokens_in, "output": s.tokens_out},
        "cache": {
            "read": s.cache_read_total,
            "creation": s.cache_creation_total,
            "uncached": s.uncached_total,
            "hit_rate": round(s.cache_hit_rate, 4),
        },
        "tree": tree,
        "thinking": [{"agent": a, "text": t} for a, t in list(s.thinking_feed)],
        "agents": agents,
        "idle_seconds": round(now - s.last_activity_at, 1) if s.last_activity_at else None,
        "companion": companion,
        "gate": _gate_to_dict(getattr(s, "pending_gate", None)),
        "paused": bool(getattr(s, "paused", False)),
        # The live path always has token/cache telemetry; flag it explicitly so the
        # shape matches empty_state_dict() / the keyless snapshot (browser hides the
        # token/cache cards only when keyless is true).
        "keyless": False,
        # interactive is set by the server (it knows whether input is enabled).
    }
