"""Tests for the keyless, session-backed WebUI.

Covers the session→snapshot mapping (``build_session_snapshot``) and an
end-to-end check that the file-backed ``WebUIServer`` serves that snapshot over
HTTP/SSE with no live runtime, no EventBus, and no API key.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

from arbor.webui.launcher import start_session_webui
from arbor.webui.server import WebUIServer
from arbor.webui.session_source import build_session_snapshot


def _write_session(root: Path) -> Path:
    """Build a minimal on-disk session with a small Idea Tree."""
    session = root / ".arbor" / "sessions" / "run_a"
    coord = session / ".coordinator"
    coord.mkdir(parents=True)
    (session / "run_info.json").write_text(
        json.dumps({"run_name": "run_a", "task": "Improve score", "cwd": str(root), "model": "host-model"}),
        encoding="utf-8",
    )
    (coord / "idea_tree.json").write_text(
        json.dumps({
            "version": 3,
            "root_id": "ROOT",
            "meta": {"baseline_score": 0.2, "trunk_score": 0.55, "metric_direction": "maximize"},
            "nodes": {
                "ROOT": {"id": "ROOT", "depth": 0, "children_ids": ["1", "2"]},
                "1": {"id": "1", "parent_id": "ROOT", "depth": 1, "status": "merged",
                      "score": 0.55, "hypothesis": "tune lr", "code_ref": "exp/n1"},
                "2": {"id": "2", "parent_id": "ROOT", "depth": 1, "status": "pruned",
                      "hypothesis": "bad idea", "insight": "did not help"},
            },
        }),
        encoding="utf-8",
    )
    return session


def test_snapshot_maps_tree_counters_and_scores(tmp_path: Path) -> None:
    session = _write_session(tmp_path)

    snap = build_session_snapshot(session, "run_a")

    assert snap["run_name"] == "run_a"
    assert snap["task"] == "Improve score"
    assert snap["model"] == "host-model"
    assert snap["phase"] == "monitoring"       # no activity sidecar → neutral
    assert snap["keyless"] is True             # browser hides token/cache panels
    assert snap["counters"] == {"proposed": 2, "done": 0, "pruned": 1, "merged": 1, "running": 0}
    assert snap["baseline_score"] == 0.2
    assert snap["best_score"] == 0.55          # from meta.trunk_score
    assert snap["metric_direction"] == "maximize"
    ids = {n["node_id"]: n for n in snap["tree"]}
    assert set(ids) == {"1", "2"}              # root excluded
    assert ids["1"]["status"] == "merged" and ids["1"]["branch"] == "exp/n1"
    assert ids["2"]["status"] == "pruned"


def test_snapshot_uses_activity_sidecar_for_timing_and_phase(tmp_path: Path) -> None:
    """With an activity sidecar present, the snapshot exposes a real run clock,
    per-node runtime, an inferred phase, a running 'agent', and a non-empty
    improvement trajectory."""
    import time

    from arbor.mcp import session_timing

    session = _write_session(tmp_path)
    coord = session / ".coordinator"
    start = time.time() - 120.0
    # Hand-write a sidecar: node 1 finished 60s in; node 3 is still running.
    (coord / session_timing.SIDECAR_NAME).write_text(
        json.dumps({
            "session_started_at": start,
            "updated_at": start + 90,
            "nodes": {
                "1": {"created_at": start + 5, "started_at": start + 10, "finished_at": start + 60},
                "3": {"created_at": start + 70, "started_at": start + 80},
            },
            "events": [
                {"ts": start + 5, "kind": "proposed", "node_id": "1"},
                {"ts": start + 60, "kind": "merged", "node_id": "1", "score": 0.55},
                {"ts": start + 80, "kind": "running", "node_id": "3"},
            ],
        }),
        encoding="utf-8",
    )
    # Add a running node so phase/agents reflect an in-flight experiment.
    tree = json.loads((coord / "idea_tree.json").read_text(encoding="utf-8"))
    tree["nodes"]["3"] = {"id": "3", "parent_id": "ROOT", "depth": 1,
                          "status": "running", "hypothesis": "try fp16\nsecond line",
                          "code_ref": "exp/n3"}
    tree["nodes"]["ROOT"]["children_ids"].append("3")
    (coord / "idea_tree.json").write_text(json.dumps(tree), encoding="utf-8")

    snap = build_session_snapshot(session, "run_a")

    assert snap["elapsed_seconds"] > 100            # real clock from session start
    assert snap["phase"] == "benchmark"             # a node is running
    by_id = {n["node_id"]: n for n in snap["tree"]}
    assert by_id["1"]["runtime_seconds"] == 50.0    # 60 - 10
    assert by_id["1"]["finished_elapsed"] == 60.0   # 60 since session start
    assert by_id["3"]["runtime_seconds"] is not None and by_id["3"]["finished_elapsed"] is None
    assert "exp/n3" in snap["agents"]               # running node surfaced as agent
    assert snap["best_score_history"] == [0.55]     # one completed point


def test_snapshot_handles_missing_session_gracefully(tmp_path: Path) -> None:
    snap = build_session_snapshot(tmp_path / "nope", "ghost")
    assert snap["run_name"] == "ghost"
    assert snap["tree"] == []
    assert snap["counters"]["proposed"] == 0


def test_file_backed_server_is_readonly_and_needs_no_bus(tmp_path: Path) -> None:
    session = _write_session(tmp_path)

    def _snap() -> dict:
        return build_session_snapshot(session, "run_a")

    server = WebUIServer(None, None, port=0, snapshot_fn=_snap)
    # File-backed mode is always read-only regardless of enable_input intent.
    assert server.interactive is False
    frame = json.loads(server.snapshot_frame())
    assert frame["kind"] == "snapshot"
    assert {n["node_id"] for n in frame["state"]["tree"]} == {"1", "2"}


def test_end_to_end_server_serves_session_over_http(tmp_path: Path) -> None:
    session = _write_session(tmp_path)
    server = start_session_webui(session, run_name="run_a", preferred=8800, scan=40)
    if server is None:
        pytest.skip("no free port to bind the WebUI in this environment")
    try:
        with urllib.request.urlopen(f"{server.url}/healthz", timeout=5) as resp:
            assert resp.read().decode() == "ok"
        # The SSE stream sends a snapshot immediately on connect.
        with urllib.request.urlopen(f"{server.url}/events", timeout=5) as resp:
            line = b""
            while b"\n\n" not in line:
                line += resp.read(1)
            payload = json.loads(line.decode().split("data: ", 1)[1])
        assert payload["kind"] == "snapshot"
        assert payload["state"]["run_name"] == "run_a"
        assert {n["node_id"] for n in payload["state"]["tree"]} == {"1", "2"}
    finally:
        server.stop()
