from __future__ import annotations

import base64
import json
import re
from pathlib import Path

from arbor.export import _read_text, export_session, resolve_session_dir


def _write_sample_session(root: Path) -> Path:
    project = root / "project"
    session_dir = project / ".arbor" / "sessions" / "run_a"
    coord = session_dir / ".coordinator"
    coord.mkdir(parents=True)
    (session_dir / "REPORT.md").write_text("# Sample Report\n\nResult summary.\n", encoding="utf-8")
    (session_dir / "run_info.json").write_text(
        json.dumps({"run_name": "run_a", "task": "Improve score", "cwd": str(project), "model": "test-model"}),
        encoding="utf-8",
    )
    (session_dir / "run_stats.json").write_text(
        json.dumps({
            "all_agents": {
                "total_llm_calls": 3,
                "total_input_tokens": 100,
                "total_output_tokens": 25,
            },
            "iterations": {"best_score": 0.42},
        }),
        encoding="utf-8",
    )
    (coord / "idea_tree.json").write_text(
        json.dumps({
            "root_id": "ROOT",
            "meta": {"baseline_score": 0.2, "trunk_score": 0.42},
            "nodes": {
                "ROOT": {"id": "ROOT", "depth": 0, "children_ids": ["n1"]},
                "n1": {"id": "n1", "depth": 1, "status": "merged", "score": 0.42},
            },
        }),
        encoding="utf-8",
    )
    (coord / "idea_tree.md").write_text("# Tree\n\n- n1 merged\n", encoding="utf-8")
    exp_dir = session_dir / "experiments" / "n1"
    exp_dir.mkdir(parents=True)
    (exp_dir / "report.md").write_text("# Experiment n1\n\nExecutor report.\n", encoding="utf-8")
    (exp_dir / "metrics.json").write_text(
        json.dumps({"node_id": "n1", "score": 0.42, "status": "merged"}),
        encoding="utf-8",
    )
    (exp_dir / "diff.patch").write_text("diff --git a/model.py b/model.py\n", encoding="utf-8")
    (session_dir / "submission.csv").write_text("id,pred\n1,0\n", encoding="utf-8")
    submissions_dir = session_dir / "submissions"
    submissions_dir.mkdir()
    (submissions_dir / "n1.csv").write_text("id,pred\n1,1\n", encoding="utf-8")
    (session_dir / "events.jsonl").write_text(
        "\n".join([
            json.dumps({"ts": "2026-06-16T00:00:00Z", "type": "session.start", "data": {"task": "Improve score"}}),
            json.dumps({"ts": "2026-06-16T00:01:00Z", "type": "idea.merged", "data": {"node_id": "n1"}}),
        ]) + "\n",
        encoding="utf-8",
    )
    return session_dir


def _decode_export_payload(html: str) -> dict:
    match = re.search(r'<script id="session-data" type="application/json">([^<]+)</script>', html)
    assert match is not None
    return json.loads(base64.b64decode(match.group(1)).decode("utf-8"))


def test_export_session_html_contains_self_contained_payload(tmp_path: Path) -> None:
    session_dir = _write_sample_session(tmp_path)

    result = export_session(session_dir)

    assert result.format == "html"
    assert result.path == session_dir / "arbor-session-run_a.html"
    html = result.path.read_text(encoding="utf-8")
    assert "Arbor Export" in html
    payload = _decode_export_payload(html)
    assert payload["summary"]["task"] == "Improve score"
    assert payload["summary"]["total_llm_calls"] == 3
    assert payload["summary"]["total_input_tokens"] == 100
    assert payload["summary"]["total_output_tokens"] == 25
    assert payload["summary"]["status_counts"] == {"merged": 1}
    assert {f["relative_path"] for f in payload["files"]} == {
        "REPORT.md",
        ".coordinator/idea_tree.md",
        "run_stats.json",
        "run_info.json",
        ".coordinator/idea_tree.json",
        "events.jsonl",
        "experiments/n1/diff.patch",
        "experiments/n1/metrics.json",
        "experiments/n1/report.md",
        "submission.csv",
        "submissions/n1.csv",
    }
    subagent_files = [f for f in payload["files"] if f["scope"] == "subagent"]
    assert {f["node_id"] for f in subagent_files} == {"n1"}
    assert all(f["default_open"] is False for f in subagent_files)
    events = next(f for f in payload["files"] if f["relative_path"] == "events.jsonl")
    assert events["records_total"] == 2
    assert len(events["records_preview"]) == 2
    assert html.index('section id="tree"') < html.index('section id="overview"')


def test_export_session_jsonl_uses_output_extension(tmp_path: Path) -> None:
    session_dir = _write_sample_session(tmp_path)
    output = tmp_path / "bundle.jsonl"

    result = export_session(session_dir, output)

    assert result.format == "jsonl"
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["type"] == "arbor.session_export"
    assert rows[0]["summary"]["session_name"] == "run_a"
    assert {row["relative_path"] for row in rows[1:]} >= {"REPORT.md", "events.jsonl"}
    metrics = next(row for row in rows[1:] if row["relative_path"] == "experiments/n1/metrics.json")
    assert metrics["scope"] == "subagent"
    assert metrics["node_id"] == "n1"
    assert metrics["json"]["score"] == 0.42


def test_resolve_session_dir_accepts_session_name_under_cwd(tmp_path: Path) -> None:
    session_dir = _write_sample_session(tmp_path)
    project = tmp_path / "project"

    assert resolve_session_dir(Path("run_a"), project) == session_dir


def test_export_session_reads_legacy_root_idea_tree_layout(tmp_path: Path) -> None:
    session_dir = tmp_path / "legacy_run"
    session_dir.mkdir()
    (session_dir / "idea_tree.json").write_text(
        json.dumps({
            "root_id": "ROOT",
            "meta": {"baseline_score": 1.0, "trunk_score": 2.0},
            "nodes": {"ROOT": {"id": "ROOT", "depth": 0}},
        }),
        encoding="utf-8",
    )
    (session_dir / "idea_tree.md").write_text("# Legacy tree\n", encoding="utf-8")

    result = export_session(session_dir)
    payload = _decode_export_payload(result.path.read_text(encoding="utf-8"))

    assert payload["summary"]["baseline_score"] == 1.0
    assert payload["summary"]["trunk_score"] == 2.0
    assert {f["relative_path"] for f in payload["files"]} == {"idea_tree.md", "idea_tree.json"}


def test_export_html_escapes_xss_from_session_data(tmp_path: Path) -> None:
    """Security regression: a session carries untrusted LLM/user text. None of it
    may render as live HTML in the shared export — it must be escaped (templated
    fields) or base64-wrapped (the data payload the browser renders via esc())."""
    xss = '<script>alert("xss")</script><img src=x onerror="alert(1)">'
    session_dir = tmp_path / "xss_run"
    coord = session_dir / ".coordinator"
    coord.mkdir(parents=True)
    (session_dir / "run_info.json").write_text(
        json.dumps({"run_name": "xss_run", "task": xss, "model": xss, "cwd": xss}),
        encoding="utf-8",
    )
    (session_dir / "REPORT.md").write_text(f"# {xss}\n", encoding="utf-8")
    (coord / "idea_tree.json").write_text(
        json.dumps({
            "root_id": "ROOT", "meta": {},
            "nodes": {
                "ROOT": {"id": "ROOT", "depth": 0, "children_ids": ["n1"]},
                "n1": {"id": "n1", "depth": 1, "status": "done", "score": 0.5,
                       "hypothesis": xss, "result": xss, "insight": xss},
            },
        }),
        encoding="utf-8",
    )

    html = export_session(session_dir).path.read_text(encoding="utf-8")

    # No executable form of the payload anywhere in the document.
    assert "<script>alert(" not in html
    assert '<img src=x onerror="' not in html
    # The data still survives intact in the base64 payload (rendered via esc()).
    assert _decode_export_payload(html)["summary"]["task"] == xss
    # The directly-templated fields (model/cwd) appear HTML-escaped.
    assert "&lt;script&gt;" in html


def test_read_text_caps_large_files(tmp_path: Path) -> None:
    """A huge artifact is tail-truncated, not slurped whole into the export."""
    big = tmp_path / "big.log"
    big.write_text("A" * 50 + "TAIL_MARKER", encoding="utf-8")  # 61 bytes
    out = _read_text(big, max_bytes=20)
    assert "truncated" in out
    assert out.endswith("TAIL_MARKER")     # the tail (recent output) is kept
    assert len(out) < 60


def test_read_text_small_file_unchanged(tmp_path: Path) -> None:
    small = tmp_path / "small.txt"
    small.write_text("hello world", encoding="utf-8")
    assert _read_text(small) == "hello world"

