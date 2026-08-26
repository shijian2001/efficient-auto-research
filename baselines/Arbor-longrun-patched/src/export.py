"""Session export helpers for Arbor runs.

The native run writes a directory of durable artifacts rather than pi's single
session JSONL file.  This module keeps export read-only: it resolves a session
directory, collects the existing artifacts, and writes either a self-contained
HTML review page or a JSONL bundle.
"""

from __future__ import annotations

import base64
import html
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ._app import APP_NAME, CONFIG_DIR_NAME


class ExportError(RuntimeError):
    """Raised when a session cannot be resolved or exported."""


@dataclass(frozen=True)
class SessionExport:
    """Metadata returned after a successful export."""

    path: Path
    format: str
    session_dir: Path


def resolve_session_dir(session: Path, cwd: Path | None = None) -> Path:
    """Resolve a session path or name under ``<cwd>/.arbor/sessions``.

    Mirrors the existing ``arbor report`` resolution behavior, but raises a
    structured ``ExportError`` so both the top-level CLI and live dashboard can
    surface a concise message.
    """

    cwd = (cwd or Path(".")).expanduser().resolve()
    candidate = Path(session).expanduser()
    candidates: list[Path] = []
    if candidate.is_absolute():
        candidates.append(candidate)
    else:
        candidates.extend([
            candidate,
            cwd / candidate,
            cwd / CONFIG_DIR_NAME / "sessions" / str(session),
        ])
        # When invoked from a linked git worktree, keyless sessions are anchored
        # at the main working tree (see session_ops._stable_arbor_base), so look
        # there too — otherwise a session created by the host agent would appear
        # "missing" the moment the dashboard is launched from the main checkout.
        try:
            from .mcp.session_ops import _stable_arbor_base

            stable = _stable_arbor_base(cwd)
            if stable != cwd:
                candidates.append(stable / CONFIG_DIR_NAME / "sessions" / str(session))
        except Exception as exc:  # best-effort: never block session lookup
            logging.getLogger(__name__).debug(
                "stable-base session lookup skipped: %s", exc)

    for path in candidates:
        if path.exists() and path.is_dir():
            return path.resolve()

    raise ExportError(f"session not found: {session}")


def export_session(
    session_dir: Path,
    output_path: Path | None = None,
    *,
    fmt: str | None = None,
) -> SessionExport:
    """Export ``session_dir`` to HTML or JSONL and return the written path."""

    session_dir = Path(session_dir).expanduser().resolve()
    if not session_dir.is_dir():
        raise ExportError(f"session directory not found: {session_dir}")

    export_format = _resolve_format(output_path, fmt)
    if output_path is None:
        output_path = session_dir / f"{APP_NAME}-session-{_safe_name(session_dir.name)}.{export_format}"
    else:
        output_path = Path(output_path).expanduser()
        if not output_path.is_absolute():
            output_path = Path.cwd() / output_path

    output_path.parent.mkdir(parents=True, exist_ok=True)
    data = _collect_session_data(session_dir)

    if export_format == "jsonl":
        _write_jsonl_export(data, output_path)
    elif export_format == "html":
        output_path.write_text(_render_html(data), encoding="utf-8")
    else:  # defensive; _resolve_format already validates
        raise ExportError(f"unsupported export format: {export_format}")

    return SessionExport(path=output_path.resolve(), format=export_format, session_dir=session_dir)


def _resolve_format(output_path: Path | None, fmt: str | None) -> str:
    if fmt:
        normalized = fmt.lower().lstrip(".")
        if normalized not in {"html", "jsonl"}:
            raise ExportError("format must be 'html' or 'jsonl'")
        return normalized
    if output_path is not None and str(output_path).lower().endswith(".jsonl"):
        return "jsonl"
    return "html"


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return safe.strip("-") or "run"


_TEXT_ARTIFACT_SUFFIXES = {
    ".csv",
    ".diff",
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".patch",
    ".rst",
    ".text",
    ".tsv",
    ".txt",
    ".yaml",
    ".yml",
}


def _collect_session_data(session_dir: Path) -> dict[str, Any]:
    coordinator_dir = session_dir / ".coordinator"
    tree_json_path = _first_existing(
        coordinator_dir / "idea_tree.json",
        session_dir / "idea_tree.json",
    )
    tree_md_path = _first_existing(
        coordinator_dir / "idea_tree.md",
        session_dir / "idea_tree.md",
    )
    checkpoint_path = _first_existing(
        coordinator_dir / "checkpoint.json",
        session_dir / "checkpoint.json",
    )
    artifacts = [
        ("report", "REPORT.md", session_dir / "REPORT.md", "markdown", True),
        ("summary_report", "summary_report.md", session_dir / "summary_report.md", "markdown", False),
        ("readme", "README.md", session_dir / "README.md", "markdown", False),
        ("coordinator_final_report", "COORDINATOR_FINAL_REPORT.txt", session_dir / "COORDINATOR_FINAL_REPORT.txt", "text", False),
        ("legacy_final_report", "final_report.txt", session_dir / "final_report.txt", "text", False),
        ("conversation", "conversation.md", session_dir / "conversation.md", "markdown", False),
        ("idea_tree_markdown", _relative_to(tree_md_path, session_dir), tree_md_path, "markdown", False),
        ("run_stats", "run_stats.json", session_dir / "run_stats.json", "json", False),
        ("run_info", "run_info.json", session_dir / "run_info.json", "json", False),
        ("idea_tree", _relative_to(tree_json_path, session_dir), tree_json_path, "json", False),
        ("checkpoint", _relative_to(checkpoint_path, session_dir), checkpoint_path, "json", False),
        ("events", "events.jsonl", session_dir / "events.jsonl", "jsonl", False),
        ("full_output", "full_output.log", session_dir / "full_output.log", "text", False),
        ("submission", "submission.csv", session_dir / "submission.csv", "text", False),
    ]

    files: list[dict[str, Any]] = []
    events: list[Any] = []
    for key, label, path, kind, default_open in artifacts:
        if not path.exists() or not path.is_file():
            continue
        entry, records = _build_artifact_entry(
            key=key,
            label=label,
            path=path,
            kind=kind,
            session_dir=session_dir,
            scope="main",
            default_open=default_open,
        )
        if key == "events":
            events = records
        files.append(entry)

    for path in _iter_experiment_artifacts(session_dir / "experiments"):
        rel = _relative_to(path, session_dir)
        parts = Path(rel).parts
        node_id = parts[1] if len(parts) > 1 and parts[0] == "experiments" else None
        entry, _ = _build_artifact_entry(
            key=f"experiment:{rel}",
            label=rel,
            path=path,
            kind=_kind_for_artifact(path),
            session_dir=session_dir,
            scope="subagent",
            node_id=node_id,
            default_open=False,
        )
        files.append(entry)

    for path in _iter_submission_artifacts(session_dir / "submissions"):
        rel = _relative_to(path, session_dir)
        node_id = path.stem or None
        entry, _ = _build_artifact_entry(
            key=f"submission:{rel}",
            label=rel,
            path=path,
            kind=_kind_for_artifact(path),
            session_dir=session_dir,
            scope="subagent",
            node_id=node_id,
            default_open=False,
        )
        files.append(entry)

    by_key = {f["key"]: f for f in files}
    run_info = _as_dict(by_key.get("run_info", {}).get("json"))
    run_stats = _as_dict(by_key.get("run_stats", {}).get("json"))
    idea_tree = _as_dict(by_key.get("idea_tree", {}).get("json"))

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "session_dir": str(session_dir),
        "session_name": session_dir.name,
        "run_info": run_info,
        "run_stats": run_stats,
        "idea_tree": idea_tree,
        "events": _tail(events, 200),
        "files": files,
        "summary": _build_summary(session_dir, run_info, run_stats, idea_tree, events, files),
    }


def _build_artifact_entry(
    *,
    key: str,
    label: str,
    path: Path,
    kind: str,
    session_dir: Path,
    scope: str,
    node_id: str | None = None,
    default_open: bool = False,
) -> tuple[dict[str, Any], list[Any]]:
    text = _read_text(path)
    entry: dict[str, Any] = {
        "key": key,
        "label": label,
        "path": str(path),
        "relative_path": _relative_to(path, session_dir),
        "kind": kind,
        "scope": scope,
        "size": path.stat().st_size,
        "default_open": default_open,
        "text": text,
    }
    if node_id:
        entry["node_id"] = node_id
    if kind == "json":
        entry["json"] = _loads_json(text)
    if kind == "jsonl":
        records = _loads_jsonl(text)
        entry["records_total"] = len(records)
        entry["records_preview"] = _tail(records, 200)
        return entry, records
    return entry, []


def _iter_experiment_artifacts(experiments_dir: Path) -> list[Path]:
    if not experiments_dir.is_dir():
        return []
    paths: list[Path] = []
    for path in experiments_dir.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix.lower() in _TEXT_ARTIFACT_SUFFIXES:
            paths.append(path)
    return sorted(paths, key=lambda p: p.relative_to(experiments_dir).parts)


def _iter_submission_artifacts(submissions_dir: Path) -> list[Path]:
    if not submissions_dir.is_dir():
        return []
    paths: list[Path] = []
    for path in submissions_dir.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix.lower() in {".csv", ".json", ".jsonl", ".txt"}:
            paths.append(path)
    return sorted(paths, key=lambda p: p.relative_to(submissions_dir).parts)


def _kind_for_artifact(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix == ".jsonl":
        return "jsonl"
    if suffix in {".md", ".markdown", ".rst"}:
        return "markdown"
    if suffix in {".diff", ".patch"}:
        return "diff"
    return "text"


def _first_existing(*paths: Path) -> Path:
    for path in paths:
        if path.exists():
            return path
    return paths[0]


#: Cap per-artifact text so one giant log (multi-day runs produce 100 MB+
#: ``full_output.log`` files) can't blow up memory or produce an unopenable
#: HTML. The tail is kept — the most recent output is what matters on a log.
_MAX_ARTIFACT_BYTES = 10 * 1024 * 1024  # 10 MiB


def _read_text(path: Path, max_bytes: int = _MAX_ARTIFACT_BYTES) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    if size > max_bytes:
        with open(path, "rb") as fh:
            fh.seek(size - max_bytes)
            raw = fh.read()
        dropped = size - max_bytes
        return f"[... truncated {dropped:,} earlier bytes ...]\n" + raw.decode(
            "utf-8", errors="replace"
        )
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def _relative_to(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _loads_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _loads_jsonl(text: str) -> list[Any]:
    rows: list[Any] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"_parse_error": True, "line": line})
    return rows


def _tail(values: list[Any], limit: int) -> list[Any]:
    if limit <= 0:
        return []
    return values[-limit:]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _build_summary(
    session_dir: Path,
    run_info: dict[str, Any],
    run_stats: dict[str, Any],
    idea_tree: dict[str, Any],
    events: list[Any],
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    meta = _as_dict(idea_tree.get("meta"))
    nodes = _as_dict(idea_tree.get("nodes"))
    all_agents = _as_dict(run_stats.get("all_agents"))
    iterations = _as_dict(run_stats.get("iterations"))
    status_counts: dict[str, int] = {}
    scored_nodes = 0
    for node in nodes.values():
        if not isinstance(node, dict) or node.get("depth", 0) == 0:
            continue
        status = str(node.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        if isinstance(node.get("score"), (int, float)):
            scored_nodes += 1

    task = run_info.get("task") or _first_event_data(events, "session.start").get("task") or ""
    cwd = run_info.get("cwd") or _first_event_data(events, "session.start").get("cwd") or ""
    model = (
        run_stats.get("model")
        or run_info.get("model")
        or _first_event_data(events, "session.start").get("model")
        or ""
    )

    return {
        "session_name": session_dir.name,
        "task": task,
        "cwd": cwd,
        "model": model,
        "baseline_score": meta.get("baseline_score"),
        "trunk_score": meta.get("trunk_score"),
        "test_baseline_score": meta.get("test_baseline_score"),
        "test_trunk_score": meta.get("test_trunk_score"),
        "best_score": iterations.get("best_score"),
        "total_nodes": len(nodes),
        "scored_nodes": scored_nodes,
        "status_counts": status_counts,
        "event_count": len(events),
        "total_llm_calls": all_agents.get("total_llm_calls"),
        "total_input_tokens": all_agents.get("total_input_tokens"),
        "total_output_tokens": all_agents.get("total_output_tokens"),
        "artifact_count": len(files),
    }


def _first_event_data(events: list[Any], event_type: str) -> dict[str, Any]:
    for event in events:
        if isinstance(event, dict) and event.get("type") == event_type:
            data = event.get("data")
            return data if isinstance(data, dict) else {}
    return {}


def _write_jsonl_export(data: dict[str, Any], output_path: Path) -> None:
    lines = [
        json.dumps({
            "type": "arbor.session_export",
            "generated_at": data["generated_at"],
            "session_dir": data["session_dir"],
            "summary": data["summary"],
        }, ensure_ascii=False),
    ]
    for file_entry in data["files"]:
        payload = {
            "type": "artifact",
            "key": file_entry["key"],
            "label": file_entry["label"],
            "relative_path": file_entry["relative_path"],
            "kind": file_entry["kind"],
            "scope": file_entry.get("scope", "main"),
            "node_id": file_entry.get("node_id"),
            "default_open": file_entry.get("default_open", False),
            "size": file_entry["size"],
            "text": file_entry["text"],
        }
        if "json" in file_entry:
            payload["json"] = file_entry["json"]
        if "records_preview" in file_entry:
            payload["records_preview"] = file_entry["records_preview"]
        if "records_total" in file_entry:
            payload["records_total"] = file_entry["records_total"]
        lines.append(json.dumps(payload, ensure_ascii=False))
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _render_html(data: dict[str, Any]) -> str:
    payload = base64.b64encode(json.dumps(data, ensure_ascii=False).encode("utf-8")).decode("ascii")
    summary = data["summary"]
    title = summary.get("task") or summary.get("session_name") or "Arbor session"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc_text(title)} - Arbor Export</title>
  <style>
{_EXPORT_CSS}
  </style>
</head>
<body>
  <div id="app">
    <aside id="sidebar">
      <div class="brand">Arbor Export</div>
      <div class="session-name">{_esc_text(summary.get("session_name") or data["session_name"])}</div>
      <input id="filter" type="search" placeholder="Filter artifacts or events">
      <nav id="nav"></nav>
    </aside>
    <main>
      <header>
        <div class="eyebrow">Session Snapshot</div>
        <h1>{_esc_text(title)}</h1>
        <div class="meta">
          <span>{_esc_text(summary.get("model") or "model unknown")}</span>
          <span>{_esc_text(summary.get("cwd") or "cwd unknown")}</span>
          <span>exported {_esc_text(data["generated_at"])}</span>
        </div>
      </header>
      <section id="tree"></section>
      <section id="overview"></section>
      <section id="artifacts"></section>
      <section id="subagent-artifacts"></section>
      <section id="events"></section>
      <section id="raw"></section>
    </main>
  </div>
  <script id="session-data" type="application/json">{payload}</script>
  <script>
{_EXPORT_JS}
  </script>
</body>
</html>
"""


def _esc_text(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


_EXPORT_CSS = r"""
:root {
  --bg: #f7f7f4;
  --panel: #ffffff;
  --panel-soft: #f0f0eb;
  --ink: #1f2428;
  --muted: #687078;
  --line: #d9d7cf;
  --sidebar: #202020;
  --sidebar-soft: #2b2b2b;
  --sidebar-ink: #eeeeea;
  --accent: #0f766e;
  --accent-soft: #dff3ef;
  --green: #15803d;
  --red: #b91c1c;
  --yellow: #a16207;
  --code: #171717;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
}
#app {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 340px minmax(0, 1fr);
}
#sidebar {
  position: sticky;
  top: 0;
  height: 100vh;
  overflow: auto;
  border-right: 1px solid #3f3f3f;
  background: var(--sidebar);
  color: var(--sidebar-ink);
  padding: 16px 14px;
}
.brand {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0;
}
.session-name {
  margin-top: 4px;
  color: #b7b7b1;
  overflow-wrap: anywhere;
}
#filter {
  width: 100%;
  margin: 16px 0 12px;
  padding: 7px 8px;
  border: 1px solid #555;
  border-radius: 4px;
  background: #151515;
  color: var(--sidebar-ink);
  font: inherit;
}
#filter:focus {
  outline: none;
  border-color: #8dd8cb;
}
#nav a {
  display: block;
  padding: 5px 7px;
  color: #d2d2cc;
  text-decoration: none;
  border-radius: 4px;
  overflow-wrap: anywhere;
}
#nav a:hover { background: var(--sidebar-soft); }
main {
  min-width: 0;
  max-width: 1120px;
  width: 100%;
  padding: 34px 32px 64px;
}
header {
  border-bottom: 1px solid var(--line);
  padding-bottom: 18px;
  margin-bottom: 20px;
}
.eyebrow {
  color: var(--accent);
  text-transform: uppercase;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .08em;
}
h1 {
  margin: 6px 0 10px;
  font-size: 24px;
  line-height: 1.25;
  letter-spacing: 0;
}
.meta {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  color: var(--muted);
}
.meta span, .pill {
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 2px 8px;
  background: var(--panel);
  max-width: 100%;
  overflow-wrap: anywhere;
}
section { margin-top: 24px; }
h2 {
  font-size: 16px;
  margin: 0 0 10px;
  letter-spacing: 0;
}
.section-note {
  margin: -4px 0 10px;
  color: var(--muted);
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 8px;
}
.metric, .panel, .artifact, .event-row, .node-artifacts {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 6px;
}
.metric { padding: 12px; }
.metric .label {
  color: var(--muted);
  font-size: 11px;
}
.metric .value {
  margin-top: 4px;
  font-size: 18px;
  font-weight: 700;
  overflow-wrap: anywhere;
}
.tree-root {
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--panel);
  overflow: hidden;
}
.tree-node {
  border-top: 1px solid var(--line);
}
.tree-node:first-child { border-top: 0; }
.tree-node > summary {
  cursor: pointer;
  list-style: none;
  display: grid;
  grid-template-columns: 12px minmax(54px, auto) auto auto minmax(0, 1fr);
  gap: 8px;
  align-items: baseline;
  padding: 8px 10px;
  background: var(--panel-soft);
}
.tree-node > summary::-webkit-details-marker { display: none; }
.tree-node > summary::before {
  content: "+";
  color: var(--muted);
  margin-right: -4px;
}
.tree-node[open] > summary::before { content: "-"; }
.tree-id {
  color: var(--accent);
  font-weight: 700;
}
.tree-title {
  overflow-wrap: anywhere;
}
.tree-detail {
  padding: 10px 12px 12px 34px;
  border-top: 1px solid var(--line);
}
.tree-detail p {
  margin: 0 0 8px;
  overflow-wrap: anywhere;
}
.tree-children {
  margin-left: 22px;
  border-left: 1px solid var(--line);
}
.status {
  border-radius: 999px;
  padding: 1px 7px;
  color: #fff;
  font-size: 11px;
}
.status-merged { background: var(--green); }
.status-done { background: var(--accent); }
.status-running { background: #2563eb; }
.status-pending { background: var(--yellow); }
.status-pruned { background: #6b7280; }
.status-needs_retry, .status-failed { background: var(--red); }
.status-unknown { background: #737373; }
.score {
  color: var(--green);
  font-weight: 700;
}
.artifact, .node-artifacts {
  margin-bottom: 9px;
  overflow: hidden;
}
.artifact > summary, .node-artifacts > summary {
  cursor: pointer;
  list-style: none;
  padding: 9px 11px;
  background: var(--panel-soft);
  display: grid;
  grid-template-columns: 12px minmax(0, 1fr) auto;
  gap: 12px;
  align-items: baseline;
}
.artifact > summary::-webkit-details-marker,
.node-artifacts > summary::-webkit-details-marker { display: none; }
.artifact > summary::before,
.node-artifacts > summary::before {
  content: "+";
  color: var(--muted);
}
.artifact[open] > summary::before,
.node-artifacts[open] > summary::before { content: "-"; }
.artifact > summary strong,
.node-artifacts > summary strong {
  overflow-wrap: anywhere;
}
.artifact-meta {
  color: var(--muted);
  white-space: nowrap;
}
.artifact-body {
  padding: 11px;
  border-top: 1px solid var(--line);
}
.node-artifact-list {
  padding: 10px;
  border-top: 1px solid var(--line);
}
.markdown, pre {
  overflow-x: auto;
}
.markdown {
  max-width: 92ch;
}
pre {
  margin: 0;
  padding: 10px;
  background: var(--code);
  color: #eeeeea;
  border-radius: 4px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
code {
  font-family: inherit;
  background: rgba(0, 0, 0, .06);
  padding: 0 3px;
  border-radius: 3px;
}
pre code {
  background: transparent;
  padding: 0;
}
.event-row {
  display: grid;
  grid-template-columns: 170px 170px minmax(0, 1fr);
  gap: 8px;
  align-items: start;
  padding: 8px 10px;
  margin-bottom: 6px;
}
.event-type { font-weight: 700; color: var(--accent); overflow-wrap: anywhere; }
.event-ts { color: var(--muted); overflow-wrap: anywhere; }
.event-data {
  margin: 0;
  padding: 0;
  background: transparent;
  color: var(--ink);
  font-size: 11px;
}
.raw-card { padding: 12px; }
.hidden { display: none !important; }
@media (max-width: 820px) {
  #app { grid-template-columns: 1fr; }
  #sidebar {
    position: relative;
    height: auto;
  }
  main { padding: 24px 16px 48px; }
  .tree-node > summary { grid-template-columns: 1fr; }
  .tree-children { margin-left: 10px; }
  .event-row { grid-template-columns: 1fr; }
  .artifact > summary, .node-artifacts > summary { grid-template-columns: 12px minmax(0, 1fr); }
  .artifact-meta { display: block; grid-column: 2; margin-top: 3px; white-space: normal; }
}
"""


_EXPORT_JS = r"""
(function () {
  "use strict";

  const raw = document.getElementById("session-data").textContent.trim();
  const binary = atob(raw);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  const data = JSON.parse(new TextDecoder("utf-8").decode(bytes));
  const files = data.files || [];

  const esc = (value) => String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

  const fmt = (value) => {
    if (value === null || value === undefined || value === "") return "-";
    if (typeof value === "number") return Number.isInteger(value) ? value.toLocaleString() : String(value);
    return String(value);
  };

  const slug = (value) => String(value || "section").replace(/[^A-Za-z0-9_-]+/g, "-");
  const sizeLabel = (bytes) => {
    const value = Number(bytes || 0);
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${Math.ceil(value / 1024)} KB`;
    return `${(value / 1024 / 1024).toFixed(1)} MB`;
  };
  const short = (value, limit) => {
    const text = String(value ?? "").replace(/\s+/g, " ").trim();
    return text.length > limit ? `${text.slice(0, limit - 1)}...` : text;
  };

  function renderOverview() {
    const s = data.summary || {};
    const metrics = [
      ["Baseline", s.baseline_score],
      ["Final", s.trunk_score],
      ["Best", s.best_score],
      ["Ideas", s.total_nodes],
      ["Scored", s.scored_nodes],
      ["Artifacts", s.artifact_count],
      ["Events", s.event_count],
      ["LLM Calls", s.total_llm_calls],
      ["Tokens", (s.total_input_tokens || 0) + (s.total_output_tokens || 0)],
    ];
    document.getElementById("overview").innerHTML = `
      <h2>Overview</h2>
      <div class="grid">
        ${metrics.map(([label, value]) => `
          <div class="metric">
            <div class="label">${esc(label)}</div>
            <div class="value">${esc(fmt(value))}</div>
          </div>
        `).join("")}
      </div>
    `;
  }

  function statusClass(status) {
    const normalized = String(status || "unknown").replace(/[^A-Za-z0-9_-]+/g, "_");
    return `status status-${normalized}`;
  }

  function renderTree() {
    const tree = data.idea_tree || {};
    const nodes = tree.nodes || {};
    const rootId = tree.root_id || "ROOT";
    const meta = tree.meta || {};
    const ids = Object.keys(nodes);
    if (!ids.length) {
      document.getElementById("tree").innerHTML = `
        <h2>Idea Tree</h2>
        <div class="panel raw-card">No idea tree artifact found.</div>
      `;
      return;
    }

    const childrenByParent = {};
    for (const [id, node] of Object.entries(nodes)) {
      const parent = node && node.parent_id;
      if (parent && nodes[parent]) {
        (childrenByParent[parent] ||= []).push(id);
      }
    }
    for (const node of Object.values(nodes)) {
      for (const child of node.children_ids || []) {
        if (nodes[child]) {
          const bucket = (childrenByParent[node.id] ||= []);
          if (!bucket.includes(child)) bucket.push(child);
        }
      }
    }
    for (const bucket of Object.values(childrenByParent)) {
      bucket.sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
    }

    function nodeHtml(id, depth, seen) {
      if (seen.has(id)) return "";
      seen.add(id);
      const node = nodes[id] || {};
      const status = node.status || "unknown";
      const score = node.score === null || node.score === undefined ? "" : node.score;
      const hypothesis = node.hypothesis || node.idea || "";
      const result = node.result || "";
      const insight = node.insight || "";
      const codeRef = node.code_ref || "";
      const relatedWork = node.related_work || "";
      const childIds = childrenByParent[id] || [];
      const open = depth === 0 ? " open" : "";
      const title = short(hypothesis || id, depth === 0 ? 120 : 96);
      const delta = typeof score === "number" && typeof meta.trunk_score === "number" && depth > 0
        ? ` (${score - meta.trunk_score >= 0 ? "+" : ""}${(score - meta.trunk_score).toFixed(4)})`
        : "";
      const details = [
        hypothesis ? `<p><strong>Hypothesis</strong><br>${esc(hypothesis)}</p>` : "",
        result ? `<p><strong>Result</strong><br>${esc(result)}</p>` : "",
        insight ? `<p><strong>Insight</strong><br>${esc(insight)}</p>` : "",
        relatedWork ? `<p><strong>Related work</strong><br>${esc(relatedWork)}</p>` : "",
        codeRef ? `<p><strong>Branch</strong><br><code>${esc(codeRef)}</code></p>` : "",
      ].join("");
      return `
        <details class="tree-node searchable" data-search="${esc(`${id} ${status} ${hypothesis} ${result} ${insight}`.toLowerCase())}"${open}>
          <summary>
            <span class="tree-id">${esc(id)}</span>
            <span class="${esc(statusClass(status))}">${esc(status)}</span>
            <span class="score">${score === "" ? "" : esc(`${fmt(score)}${delta}`)}</span>
            <span class="tree-title">${esc(title)}</span>
          </summary>
          ${details ? `<div class="tree-detail">${details}</div>` : ""}
          ${childIds.length ? `<div class="tree-children">${childIds.map((child) => nodeHtml(child, depth + 1, seen)).join("")}</div>` : ""}
        </details>
      `;
    }

    document.getElementById("tree").innerHTML = `
      <h2>Idea Tree</h2>
      <div class="tree-root">${nodeHtml(rootId, 0, new Set())}</div>
    `;
  }

  function renderMarkdown(text) {
    return `<div class="markdown"><pre>${esc(text || "")}</pre></div>`;
  }

  function renderArtifactBody(file) {
    if (file.kind === "json") {
      return `<pre><code>${esc(JSON.stringify(file.json ?? file.text, null, 2))}</code></pre>`;
    }
    if (file.kind === "jsonl") {
      const preview = file.records_preview;
      if (Array.isArray(preview)) {
        const total = Number(file.records_total ?? preview.length);
        const note = total > preview.length
          ? `<p class="section-note">Showing latest ${preview.length} of ${total} parsed records. Full JSONL text is included in this export.</p>`
          : "";
        return `${note}<pre><code>${esc(JSON.stringify(preview, null, 2))}</code></pre>`;
      }
      return `<pre><code>${esc(file.text || "")}</code></pre>`;
    }
    return renderMarkdown(file.text || "");
  }

  function artifactSummary(file) {
    const scope = file.scope === "subagent" ? `subagent${file.node_id ? `:${file.node_id}` : ""}` : "main";
    return `
      <strong>${esc(file.label || file.relative_path)}</strong>
      <span class="artifact-meta">${esc(scope)} | ${esc(file.kind)} | ${esc(sizeLabel(file.size))}</span>
    `;
  }

  function artifactDetails(file, index, forceClosed) {
    const id = `artifact-${slug(file.key || file.relative_path || index)}`;
    const open = !forceClosed && file.default_open ? " open" : "";
    const search = `${file.label || ""} ${file.relative_path || ""} ${file.kind || ""} ${file.scope || ""} ${file.node_id || ""}`;
    return `
      <details class="artifact searchable" id="${id}" data-file-index="${index}" data-search="${esc(search.toLowerCase())}"${open}>
        <summary>${artifactSummary(file)}</summary>
        <div class="artifact-body"></div>
      </details>
    `;
  }

  function hydrateArtifact(el) {
    const body = el.querySelector(".artifact-body");
    if (!body || body.dataset.rendered === "1") return;
    const index = Number(el.getAttribute("data-file-index"));
    body.innerHTML = renderArtifactBody(files[index] || {});
    body.dataset.rendered = "1";
  }

  function installArtifactHydration(root) {
    root.querySelectorAll(".artifact").forEach((el) => {
      if (el.open) hydrateArtifact(el);
      el.addEventListener("toggle", () => {
        if (el.open) hydrateArtifact(el);
      });
    });
  }

  function renderArtifacts() {
    const main = files
      .map((file, index) => [file, index])
      .filter(([file]) => file.scope !== "subagent");
    const root = document.getElementById("artifacts");
    root.innerHTML = `
      <h2>Main Artifacts</h2>
      ${main.map(([file, index]) => artifactDetails(file, index, false)).join("") || `<div class="panel raw-card">No main artifacts found.</div>`}
    `;
    installArtifactHydration(root);
  }

  function renderSubagentArtifacts() {
    const grouped = new Map();
    files.forEach((file, index) => {
      if (file.scope !== "subagent") return;
      const nodeId = file.node_id || "unknown";
      if (!grouped.has(nodeId)) grouped.set(nodeId, []);
      grouped.get(nodeId).push([file, index]);
    });
    const root = document.getElementById("subagent-artifacts");
    const groups = Array.from(grouped.entries()).sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }));
    root.innerHTML = `
      <h2>Subagent Artifacts</h2>
      <p class="section-note">Executor artifacts are folded by default.</p>
      ${groups.map(([nodeId, rows]) => `
        <details class="node-artifacts searchable" data-search="${esc(`${nodeId} ${rows.map(([file]) => file.relative_path).join(" ")}`.toLowerCase())}">
          <summary>
            <strong>${esc(nodeId)}</strong>
            <span class="artifact-meta">${rows.length} files</span>
          </summary>
          <div class="node-artifact-list">
            ${rows.map(([file, index]) => artifactDetails(file, index, true)).join("")}
          </div>
        </details>
      `).join("") || `<div class="panel raw-card">No subagent artifacts found.</div>`}
    `;
    installArtifactHydration(root);
  }

  function renderEvents() {
    const events = data.events || [];
    const recent = events.slice().reverse();
    document.getElementById("events").innerHTML = `
      <h2>Recent Events</h2>
      <p class="section-note">Showing the latest ${recent.length} parsed events. Full events.jsonl is in Main Artifacts.</p>
      ${recent.map((event) => `
        <div class="event-row searchable" data-search="${esc(`${event.type || ""} ${JSON.stringify(event.data || {})}`.toLowerCase())}">
          <div class="event-ts">${esc(event.ts || "")}</div>
          <div class="event-type">${esc(event.type || "event")}</div>
          <pre class="event-data">${esc(JSON.stringify(event.data || {}, null, 2))}</pre>
        </div>
      `).join("") || `<div class="panel raw-card">No events.jsonl artifact found.</div>`}
    `;
  }

  function renderRaw() {
    const root = document.getElementById("raw");
    root.innerHTML = `
      <h2>Raw Export Data</h2>
      <details class="artifact">
        <summary><strong>Decoded payload</strong><span class="artifact-meta">render on open</span></summary>
        <div class="artifact-body"></div>
      </details>
    `;
    const details = root.querySelector("details");
    details.addEventListener("toggle", () => {
      const body = details.querySelector(".artifact-body");
      if (details.open && body.dataset.rendered !== "1") {
        body.innerHTML = `<pre><code>${esc(JSON.stringify(data, null, 2))}</code></pre>`;
        body.dataset.rendered = "1";
      }
    });
  }

  function renderNav() {
    const hasSubagents = files.some((file) => file.scope === "subagent");
    const links = [
      ["tree", "Idea Tree"],
      ["overview", "Overview"],
      ["artifacts", "Main Artifacts"],
      ...(hasSubagents ? [["subagent-artifacts", "Subagent Artifacts"]] : []),
      ["events", "Recent Events"],
      ["raw", "Raw Data"],
    ];
    document.getElementById("nav").innerHTML = links
      .map(([id, label]) => `<a href="#${id}">${esc(label)}</a>`)
      .join("");
  }

  function installFilter() {
    const input = document.getElementById("filter");
    input.addEventListener("input", () => {
      const q = input.value.trim().toLowerCase();
      document.querySelectorAll(".searchable").forEach((el) => {
        const hay = el.getAttribute("data-search") || "";
        el.classList.toggle("hidden", Boolean(q && !hay.includes(q)));
      });
      if (!q) return;
      document.querySelectorAll(".searchable:not(.hidden)").forEach((el) => {
        let parent = el.parentElement?.closest(".searchable");
        while (parent) {
          parent.classList.remove("hidden");
          if (parent instanceof HTMLDetailsElement) parent.open = true;
          parent = parent.parentElement?.closest(".searchable");
        }
      });
    });
  }

  renderTree();
  renderOverview();
  renderArtifacts();
  renderSubagentArtifacts();
  renderEvents();
  renderRaw();
  renderNav();
  installFilter();
})();
"""
