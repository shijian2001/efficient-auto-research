#!/usr/bin/env python3
"""Generate the bundled ``arbor replay --demo`` recording.

The demo lets a fresh clone run ``arbor replay --demo`` and watch the dashboard
work with **zero setup** — no API key, no model. It is an *illustrative sample*:
the trajectory mirrors what optimizing the AlgoTune k-NN example looks like
(vectorize → KD-tree → batch → dtype), but the scores are hand-authored, not a
benchmarked result. That keeps the demo honest — it showcases the UI and the
hypothesis-tree process, and makes no benchmark claim from synthetic numbers.

Re-generate after changing the event schema:

    python scripts/generate_demo_recording.py

Outputs (committed as package data, see pyproject [tool.setuptools.package-data]):
    src/cli/assets/demo_session/events.jsonl
    src/cli/assets/demo_session/tree.json
"""

from __future__ import annotations

import json
from pathlib import Path

# Import the event-type constants so the recording can never drift from the
# names the live dashboard subscribes to.
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from events import types as ev  # noqa: E402  (after sys.path tweak)

OUT_DIR = REPO_ROOT / "src" / "cli" / "assets" / "demo_session"

# Fixed base time keeps the recording byte-stable across regenerations.
BASE_TS = 1_750_000_000.0

MODEL = "claude-opus-4-8"
TASK = (
    "Optimize the k-NN classifier in solver.py for throughput on the held-out "
    "queries. Do NOT modify the evaluation harness or the data files."
)


class Timeline:
    """Accumulates events with monotonically advancing timestamps."""

    def __init__(self, base_ts: float) -> None:
        self._ts = base_ts
        self.events: list[dict] = []

    def emit(self, dt: float, etype: str, data: dict) -> None:
        self._ts += dt
        self.events.append({"ts": round(self._ts, 3), "type": etype, "data": data})


def _llm(t: Timeline, agent: str, node: str, tin: int, tout: int, *, dt: float = 1.1) -> None:
    t.emit(dt, ev.LLM_CALL, {
        "provider": "anthropic", "model": MODEL,
        "input_tokens": tin, "output_tokens": tout,
        "cache_read_tokens": int(tin * 0.82), "cache_creation_tokens": int(tin * 0.1),
        "uncached_input_tokens": int(tin * 0.08), "agent": agent, "node_id": node,
    })


def _think(t: Timeline, agent: str, node: str, text: str, *, dt: float = 0.8) -> None:
    t.emit(dt, ev.THINKING_DELTA, {"agent": agent, "node_id": node, "text": text})


def _tool(t: Timeline, agent: str, node: str, name: str, preview: str,
          *, ok: bool = True, dur: float = 2.0, gap: float = 0.5) -> None:
    t.emit(gap, ev.TOOL_START, {"name": name, "args_preview": preview, "agent": agent, "node_id": node})
    t.emit(dur, ev.TOOL_END, {"name": name, "ok": ok, "duration": dur, "agent": agent, "node_id": node})


def _explore(t: Timeline, node: str, branch: str, tools: list[tuple[str, str]],
             *, score: float | None, status: str, dur: float) -> None:
    """One executor exploration: start → reason/edit/test → end."""
    agent = f"sub:{node}"
    t.emit(0.6, ev.EXECUTOR_START, {"node_id": node, "idea": "", "branch": branch})
    _think(t, agent, node, "Reading solver.py and the eval harness to find the hot path.")
    for name, preview in tools:
        _tool(t, agent, node, name, preview, dur=2.4, gap=0.6)
        _llm(t, agent, node, 9200, 540)
    _think(t, agent, node, "Running the dev split to score this change.")
    _tool(t, agent, node, "Bash", "python eval.py --split dev", dur=dur, gap=0.4)
    end = {"node_id": node, "duration": dur, "tokens": 41000, "status": status}
    if score is not None:
        end["score"] = score
    t.emit(0.5, ev.EXECUTOR_END, end)


def build() -> Timeline:
    t = Timeline(BASE_TS)
    t.emit(0.0, ev.SESSION_START, {
        "task": TASK, "cwd": "examples/algotune_knn", "provider": "anthropic", "model": MODEL,
    })

    # ── Cycle 1: vectorize the brute-force distance computation ──
    t.emit(0.4, ev.CYCLE_START, {"cycle_num": 1, "total_cycles": 8})
    t.emit(0.3, ev.PHASE_CHANGE, {"phase": "observe"})
    _think(t, "coordinator", "", "Baseline is a Python double loop over training points. "
            "The distance matrix is the obvious first target.")
    _tool(t, "coordinator", "", "TreeView", "", dur=0.6)
    t.emit(0.3, ev.PHASE_CHANGE, {"phase": "ideate"})
    t.emit(0.6, ev.IDEA_PROPOSED, {
        "node_id": "n1", "parent_id": "root",
        "hypothesis": "Vectorize pairwise distances with numpy broadcasting",
    })
    t.emit(0.3, ev.PHASE_CHANGE, {"phase": "select"})
    t.emit(0.3, ev.PHASE_CHANGE, {"phase": "dispatch"})
    _explore(t, "n1", "arbor/n1-vectorize", [
        ("ReadFile", "solver.py"),
        ("EditFile", "solver.py: replace loop with np.linalg.norm broadcast"),
    ], score=3.2, status="done", dur=6.0)
    t.emit(0.3, ev.PHASE_CHANGE, {"phase": "backprop"})
    _think(t, "coordinator", "n1", "3.2× speedup, accuracy unchanged on held-out. "
            "Broadcasting removes the Python loop overhead. Keep it.")
    t.emit(0.5, ev.IDEA_COMPLETED, {"node_id": "n1", "score": 3.2, "status": "done"})
    t.emit(0.3, ev.PHASE_CHANGE, {"phase": "decide"})
    t.emit(0.6, ev.IDEA_MERGED, {"node_id": "n1", "from_score": 1.0, "to_score": 3.2,
                                 "branch": "arbor/n1-vectorize"})
    t.emit(0.4, ev.CYCLE_END, {"cycle_num": 1, "duration": 22.0})

    # ── Cycle 2: spatial index ──
    t.emit(0.4, ev.CYCLE_START, {"cycle_num": 2, "total_cycles": 8})
    t.emit(0.3, ev.PHASE_CHANGE, {"phase": "ideate"})
    _think(t, "coordinator", "", "Vectorizing helped but it is still O(n²). A spatial "
            "index should dominate for the held-out query sizes.")
    t.emit(0.6, ev.IDEA_PROPOSED, {
        "node_id": "n2", "parent_id": "n1",
        "hypothesis": "Replace brute force with scipy.spatial.cKDTree",
    })
    t.emit(0.3, ev.PHASE_CHANGE, {"phase": "dispatch"})
    _explore(t, "n2", "arbor/n2-ckdtree", [
        ("ReadFile", "solver.py"),
        ("EditFile", "solver.py: build cKDTree(train), query(k)"),
    ], score=8.7, status="done", dur=5.4)
    t.emit(0.3, ev.PHASE_CHANGE, {"phase": "backprop"})
    _think(t, "coordinator", "n2", "8.7×. The KD-tree turns the query into O(log n). "
            "Big win — merge onto the trunk.")
    t.emit(0.5, ev.IDEA_COMPLETED, {"node_id": "n2", "score": 8.7, "status": "done"})
    t.emit(0.6, ev.IDEA_MERGED, {"node_id": "n2", "from_score": 3.2, "to_score": 8.7,
                                 "branch": "arbor/n2-ckdtree"})
    t.emit(0.4, ev.CYCLE_END, {"cycle_num": 2, "duration": 19.0})

    # ── Cycle 3: a branch that loses (numba conflicts with the C tree) ──
    t.emit(0.4, ev.CYCLE_START, {"cycle_num": 3, "total_cycles": 8})
    t.emit(0.3, ev.PHASE_CHANGE, {"phase": "ideate"})
    t.emit(0.6, ev.IDEA_PROPOSED, {
        "node_id": "n3", "parent_id": "n2",
        "hypothesis": "JIT-compile a custom distance kernel with numba @njit",
    })
    t.emit(0.3, ev.PHASE_CHANGE, {"phase": "dispatch"})
    _explore(t, "n3", "arbor/n3-numba", [
        ("EditFile", "solver.py: add @njit kernel"),
    ], score=6.1, status="done", dur=7.2)
    t.emit(0.3, ev.PHASE_CHANGE, {"phase": "backprop"})
    _think(t, "coordinator", "n3", "6.1× — slower than the cKDTree trunk (8.7×). The JIT "
            "kernel re-introduces the brute-force scan. Prune.")
    t.emit(0.5, ev.IDEA_PRUNED, {"node_id": "n3",
                                 "reason": "regression vs trunk (6.1× < 8.7×)"})
    t.emit(0.4, ev.CYCLE_END, {"cycle_num": 3, "duration": 18.0})

    # ── Cycle 4: batch the queries ──
    t.emit(0.4, ev.CYCLE_START, {"cycle_num": 4, "total_cycles": 8})
    t.emit(0.3, ev.PHASE_CHANGE, {"phase": "ideate"})
    t.emit(0.6, ev.IDEA_PROPOSED, {
        "node_id": "n4", "parent_id": "n2",
        "hypothesis": "Batch all query points into one cKDTree.query call",
    })
    t.emit(0.3, ev.PHASE_CHANGE, {"phase": "dispatch"})
    _explore(t, "n4", "arbor/n4-batch", [
        ("EditFile", "solver.py: vectorize query over the full batch, workers=-1"),
    ], score=11.4, status="done", dur=4.8)
    t.emit(0.3, ev.PHASE_CHANGE, {"phase": "backprop"})
    _think(t, "coordinator", "n4", "11.4×. Amortizing tree traversal across the batch and "
            "using all cores compounds with the index. Merge.")
    t.emit(0.5, ev.IDEA_COMPLETED, {"node_id": "n4", "score": 11.4, "status": "done"})
    t.emit(0.6, ev.IDEA_MERGED, {"node_id": "n4", "from_score": 8.7, "to_score": 11.4,
                                 "branch": "arbor/n4-batch"})
    t.emit(0.4, ev.CYCLE_END, {"cycle_num": 4, "duration": 16.0})

    # ── Cycle 5: dtype + memory layout ──
    t.emit(0.4, ev.CYCLE_START, {"cycle_num": 5, "total_cycles": 8})
    t.emit(0.3, ev.PHASE_CHANGE, {"phase": "ideate"})
    t.emit(0.6, ev.IDEA_PROPOSED, {
        "node_id": "n5", "parent_id": "n4",
        "hypothesis": "Use float32 + C-contiguous arrays to cut memory bandwidth",
    })
    t.emit(0.6, ev.IDEA_PROPOSED, {
        "node_id": "n6", "parent_id": "n4",
        "hypothesis": "Approximate NN via random-projection trees",
    })
    t.emit(0.3, ev.PHASE_CHANGE, {"phase": "dispatch"})
    _explore(t, "n5", "arbor/n5-float32", [
        ("EditFile", "solver.py: cast to float32, np.ascontiguousarray"),
    ], score=13.1, status="done", dur=4.2)
    t.emit(0.3, ev.PHASE_CHANGE, {"phase": "backprop"})
    _think(t, "coordinator", "n5", "13.1× and accuracy holds on the held-out split. "
            "float32 halves the bytes the tree touches. Merge.")
    t.emit(0.5, ev.IDEA_COMPLETED, {"node_id": "n5", "score": 13.1, "status": "done"})
    t.emit(0.6, ev.IDEA_MERGED, {"node_id": "n5", "from_score": 11.4, "to_score": 13.1,
                                 "branch": "arbor/n5-float32"})

    # n6 explored, but accuracy regresses on held-out → prune (the discipline payoff)
    t.emit(0.4, ev.PHASE_CHANGE, {"phase": "dispatch"})
    _explore(t, "n6", "arbor/n6-approx", [
        ("EditFile", "solver.py: random-projection forest, approx query"),
    ], score=None, status="failed", dur=5.0)
    t.emit(0.5, ev.IDEA_PRUNED, {"node_id": "n6",
                                 "reason": "held-out accuracy 0.91 < 0.98 threshold"})
    t.emit(0.4, ev.CYCLE_END, {"cycle_num": 5, "duration": 21.0})

    t.emit(0.8, ev.CONVERGENCE_REACHED, {"reason": "no improving idea in last cycle",
                                         "final_score": 13.1})
    t.emit(0.6, ev.SESSION_END, {
        "duration": round(t._ts - BASE_TS, 1), "exit_reason": "converged", "turns": 47,
        "input_tokens": 612000, "output_tokens": 38400,
    })
    return t


def tree_json() -> dict:
    """The persisted idea tree — supplies baseline + metric direction to replay."""
    def node(nid, parent, hyp, status, score, branch):
        return {"id": nid, "parent_id": parent, "hypothesis": hyp,
                "status": status, "score": score, "code_ref": branch}

    return {
        "root_id": "root",
        "meta": {"metric_direction": "maximize", "baseline_score": 1.0, "trunk_score": 13.1,
                 "metric_name": "speedup_vs_baseline"},
        "nodes": {
            "root": node("root", None, "baseline solver.py", "merged", 1.0, "main"),
            "n1": node("n1", "root", "Vectorize pairwise distances with numpy broadcasting",
                       "merged", 3.2, "arbor/n1-vectorize"),
            "n2": node("n2", "n1", "Replace brute force with scipy.spatial.cKDTree",
                       "merged", 8.7, "arbor/n2-ckdtree"),
            "n3": node("n3", "n2", "JIT-compile a custom distance kernel with numba @njit",
                       "pruned", 6.1, "arbor/n3-numba"),
            "n4": node("n4", "n2", "Batch all query points into one cKDTree.query call",
                       "merged", 11.4, "arbor/n4-batch"),
            "n5": node("n5", "n4", "Use float32 + C-contiguous arrays to cut memory bandwidth",
                       "merged", 13.1, "arbor/n5-float32"),
            "n6": node("n6", "n4", "Approximate NN via random-projection trees",
                       "pruned", None, "arbor/n6-approx"),
        },
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    timeline = build()
    events_path = OUT_DIR / "events.jsonl"
    with events_path.open("w", encoding="utf-8") as fp:
        for event in timeline.events:
            fp.write(json.dumps(event, ensure_ascii=False) + "\n")
    (OUT_DIR / "tree.json").write_text(
        json.dumps(tree_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(timeline.events)} events → {events_path}")
    print(f"wrote tree.json → {OUT_DIR / 'tree.json'}")


if __name__ == "__main__":
    main()
