"""Build a WebUI snapshot from a *durable session directory* (no live runtime).

The push-based WebUI (:mod:`arbor.webui.server`) normally renders a live
``RunState`` fed by the native runtime's ``EventBus``. When a host coding agent
drives the loop instead, there is no orchestrator and no bus — the host agent
mutates session state through the ``arbor mcp`` tools, which persist to
``.arbor/sessions/<run>/``. This module reads that on-disk state and produces the
*same* snapshot dict shape the browser already understands
(:func:`arbor.webui.snapshot.empty_state_dict`), so the existing page renders
progress with no changes.

The Idea Tree JSON has no timing, so a sibling *activity sidecar*
(``.coordinator/activity.json``, written by :mod:`arbor.mcp.session_timing`)
supplies the run clock, per-node runtime, the active pipeline phase, and the
recent-activity feed. Token/cache accounting is owned by the host harness and is
*not* observable here — the snapshot is flagged ``keyless`` so the browser hides
those panels rather than render misleading zeros.

It is read-only and polled (the server's heartbeat calls it every ~1.5s), so the
browser reflects tree updates as the host agent makes them.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..mcp import session_timing
from .snapshot import empty_state_dict

# Node statuses that count as a running executor (drives phase + the agents map).
_RUNNING = "running"
# Statuses that contribute a point to the improvement trajectory.
_SCORED_STATUSES = ("done", "merged")


def _load_json(path: Path) -> dict[str, Any]:
    """Load a JSON object, returning ``{}`` on any missing/invalid file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _first_line(text: str, limit: int = 120) -> str:
    """First non-empty line of *text*, truncated — used for compact previews."""
    for line in str(text).splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return ""


def _node_to_tree_entry(
    node: dict[str, Any],
    timing: dict[str, Any],
    now: float,
    run_started: float | None,
) -> dict[str, Any]:
    """Map a stored IdeaTree node onto the browser's tree-entry shape.

    Mirrors the live ``snapshot._idea_to_dict`` contract. ``runtime_seconds`` and
    ``finished_elapsed`` come from the activity sidecar when available (else
    None, exactly as before).
    """
    t = timing.get(node.get("id"), {}) if isinstance(timing, dict) else {}
    started = t.get("started_at")
    finished = t.get("finished_at")
    runtime = None
    if isinstance(started, (int, float)):
        end = finished if isinstance(finished, (int, float)) else now
        runtime = round(end - started, 1)
    finished_elapsed = None
    if isinstance(finished, (int, float)) and run_started is not None:
        finished_elapsed = round(finished - run_started, 1)
    return {
        "node_id": node.get("id"),
        "hypothesis": node.get("hypothesis", ""),
        "status": node.get("status", "pending"),
        "score": node.get("score"),
        "branch": node.get("code_ref"),
        "parent_id": node.get("parent_id"),
        "runtime_seconds": runtime,
        "finished_elapsed": finished_elapsed,
        "pruned_reason": node.get("pruned_reason"),
        "insight": node.get("insight") or None,
    }


def _best_score(meta: dict[str, Any], nodes: list[dict[str, Any]]) -> float | None:
    """Best score so far: prefer the recorded trunk score, else compute it.

    Falls back to the best score among completed/merged nodes, honouring
    ``metric_direction``.
    """
    trunk = meta.get("trunk_score")
    if isinstance(trunk, (int, float)):
        return float(trunk)
    scored = [
        float(n["score"])
        for n in nodes
        if isinstance(n.get("score"), (int, float)) and n.get("status") in _SCORED_STATUSES
    ]
    if not scored:
        return None
    return min(scored) if meta.get("metric_direction") == "minimize" else max(scored)


def _best_score_history(
    nodes: list[dict[str, Any]],
    timing: dict[str, Any],
    direction: str,
) -> list[float]:
    """Running-best score over completed nodes, ordered by finish time.

    This is the series the browser's "Improvement Trajectory" chart plots; with
    no live ``RunState`` it would otherwise be empty (a flat/degenerate line).
    """
    points: list[tuple[float, float]] = []
    for n in nodes:
        if n.get("status") not in _SCORED_STATUSES:
            continue
        score = n.get("score")
        if not isinstance(score, (int, float)):
            continue
        fin = timing.get(n.get("id"), {}).get("finished_at") if isinstance(timing, dict) else None
        # Nodes without a recorded finish still count — sort them last but stable.
        points.append((float(fin) if isinstance(fin, (int, float)) else float("inf"), float(score)))
    # Order by finish time; tiebreak on score so legacy sessions with no timing
    # (all keyed at +inf) still draw in a stable, deterministic order.
    points.sort(key=lambda p: (p[0], p[1]))
    history: list[float] = []
    best: float | None = None
    for _, score in points:
        if best is None:
            best = score
        else:
            best = min(best, score) if direction == "minimize" else max(best, score)
        history.append(round(best, 6))
    return history


def _derive_phase(nodes: list[dict[str, Any]], events: list[dict[str, Any]]) -> str:
    """Pick a coarse pipeline phase the browser can light up.

    Keyless runs have no coordinator loop, so we infer a plausible current stage
    from tree state + the latest activity event:

    * a node is running        → ``benchmark`` (the timing step is active)
    * last event proposed       → ``ideate``
    * last event terminal       → ``decide``
    * otherwise                  → ``observe`` (idle with a tree) / ``monitoring``
    """
    if any(n.get("status") == _RUNNING for n in nodes):
        return "benchmark"
    if not events:
        # No activity sidecar (legacy session): stay neutral rather than guess.
        return "monitoring"
    last = events[-1].get("kind")
    if last in ("done", "merged", "pruned", "needs_retry", "failed"):
        return "decide"
    if last == "proposed":
        return "ideate"
    return "observe"


def build_session_snapshot(session_dir: Path, run_name: str | None = None) -> dict[str, Any]:
    """Return a browser snapshot dict for the session at *session_dir*.

    Reads ``.coordinator/idea_tree.json`` (nodes + meta), the activity sidecar,
    and the optional ``run_info.json``. Always returns a valid snapshot — missing
    or partial sessions degrade gracefully to the empty shape.
    """
    session_dir = Path(session_dir)
    coord_dir = session_dir / ".coordinator"
    state = empty_state_dict()
    state["run_name"] = run_name or session_dir.name
    state["keyless"] = True

    run_info = _load_json(session_dir / "run_info.json")
    if run_info:
        state["task"] = run_info.get("task", "")
        state["cwd"] = run_info.get("cwd", "")
        state["model"] = run_info.get("model", "—")

    tree_json = _load_json(coord_dir / "idea_tree.json")
    nodes_map: dict[str, Any] = tree_json.get("nodes", {}) if isinstance(tree_json.get("nodes"), dict) else {}
    meta: dict[str, Any] = tree_json.get("meta", {}) if isinstance(tree_json.get("meta"), dict) else {}
    root_id = tree_json.get("root_id", "ROOT")

    # Non-root nodes, preserving insertion order for a stable display.
    nodes = [n for nid, n in nodes_map.items() if nid != root_id and isinstance(n, dict)]

    # Root hypothesis is the research task when run_info didn't supply one.
    if not state["task"]:
        root = nodes_map.get(root_id, {})
        state["task"] = root.get("hypothesis", "") if isinstance(root, dict) else ""

    # ── Timing sidecar: run clock, per-node runtime, recent activity ──────────
    sidecar = session_timing.load(coord_dir)
    timing = sidecar.get("nodes", {}) if isinstance(sidecar.get("nodes"), dict) else {}
    events = sidecar.get("events", []) if isinstance(sidecar.get("events"), list) else []
    now = time.time()
    run_started = sidecar.get("session_started_at")
    if not isinstance(run_started, (int, float)):
        # Fallback for sessions created before timing was tracked: approximate
        # the clock from the tree file's mtime.
        try:
            run_started = (coord_dir / "idea_tree.json").stat().st_mtime
        except OSError:
            run_started = None
    state["elapsed_seconds"] = round(now - run_started, 1) if isinstance(run_started, (int, float)) else 0

    def _count(status: str) -> int:
        return sum(1 for n in nodes if n.get("status") == status)

    state["counters"] = {
        "proposed": len(nodes),
        "done": _count("done"),
        "pruned": _count("pruned"),
        "merged": _count("merged"),
        "running": _count(_RUNNING),
    }
    state["baseline_score"] = meta.get("baseline_score")
    direction = meta.get("metric_direction", "maximize")
    state["metric_direction"] = direction
    state["best_score"] = _best_score(meta, nodes)
    state["best_score_history"] = _best_score_history(nodes, timing, direction)
    state["tree"] = [_node_to_tree_entry(n, timing, now, run_started) for n in nodes]
    state["phase"] = _derive_phase(nodes, events)

    # Expose running nodes as "agents" so the browser's benchmarking indicator,
    # progress estimate, and pipeline fallback have something to track.
    agents: dict[str, Any] = {}
    for n in nodes:
        if n.get("status") != _RUNNING:
            continue
        nid = n.get("id")
        started = timing.get(nid, {}).get("started_at") if isinstance(timing, dict) else None
        label = n.get("code_ref") or nid or "executor"
        agents[label] = {
            "tool": "executor",
            "node_id": nid,
            "preview": _first_line(n.get("hypothesis", "")),
            "ok": None,
            "elapsed": round(now - started, 1) if isinstance(started, (int, float)) else None,
            "duration": None,
        }
    state["agents"] = agents
    return state
