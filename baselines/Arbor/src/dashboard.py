"""Self-contained HTML dashboard generator for research runs.

Reads idea_tree.json (+ optional run_info.json) and produces a single
HTML file with embedded CSS/JS — no external dependencies. Open it in
any browser to explore results interactively.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


def generate_dashboard(
    tree_json_path: Path,
    output_path: Path,
    run_info_path: Path | None = None,
) -> Path:
    """Generate an HTML dashboard from idea_tree.json.

    Returns the output path for convenience.
    """
    tree = json.loads(tree_json_path.read_text(encoding="utf-8"))
    run_info: dict[str, Any] = {}
    if run_info_path and run_info_path.exists():
        run_info = json.loads(run_info_path.read_text(encoding="utf-8"))

    meta = tree.get("meta", {})
    nodes = tree.get("nodes", {})
    root_id = tree.get("root_id", "ROOT")

    scored = [
        n for n in nodes.values()
        if n.get("depth", 0) > 0 and n.get("score") is not None
    ]
    scored.sort(key=lambda n: n.get("score", 0), reverse=True)

    status_counts: dict[str, int] = {}
    for n in nodes.values():
        if n.get("depth", 0) == 0:
            continue
        st = n.get("status", "unknown")
        status_counts[st] = status_counts.get(st, 0) + 1

    root_node = nodes.get(root_id, {})

    doc = _build_html(
        meta=meta,
        nodes=nodes,
        root_id=root_id,
        scored=scored,
        status_counts=status_counts,
        root_node=root_node,
        run_info=run_info,
        tree_json=json.dumps(tree, ensure_ascii=False),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(doc, encoding="utf-8")
    return output_path


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    return html.escape(text or "", quote=True)


def _fmt_score(val: Any) -> str:
    if val is None:
        return "N/A"
    return f"{val:.1f}%"


def _shorten(text: str, max_len: int = 80) -> str:
    text = (text or "").replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


_STATUS_COLORS = {
    "merged": "#22c55e",
    "done": "#3b82f6",
    "running": "#f59e0b",
    "needs_retry": "#eab308",
    "pending": "#a3a3a3",
    "pruned": "#6b7280",
}

_STATUS_BG = {
    "merged": "#dcfce7",
    "done": "#dbeafe",
    "running": "#fef3c7",
    "needs_retry": "#fef9c3",
    "pending": "#f5f5f5",
    "pruned": "#f3f4f6",
}


def _build_score_cards(meta: dict, status_counts: dict[str, int]) -> str:
    baseline_dev = meta.get("baseline_score")
    trunk_dev = meta.get("trunk_score")
    baseline_test = meta.get("test_baseline_score")
    trunk_test = meta.get("test_trunk_score")

    cards = []

    def _card(label: str, before: Any, after: Any, sublabel: str = "") -> str:
        delta = None
        if before is not None and after is not None:
            delta = after - before
        delta_html = ""
        if delta is not None:
            color = "#22c55e" if delta > 0 else "#ef4444" if delta < 0 else "#a3a3a3"
            sign = "+" if delta > 0 else ""
            delta_html = f'<div class="card-delta" style="color:{color}">{sign}{delta:.1f}%</div>'

        return f"""
        <div class="score-card">
          <div class="card-label">{_esc(label)}</div>
          {f'<div class="card-sublabel">{_esc(sublabel)}</div>' if sublabel else ''}
          <div class="card-row">
            <div class="card-num-box">
              <div class="card-num-label">Baseline</div>
              <div class="card-num">{_fmt_score(before)}</div>
            </div>
            <div class="card-arrow">&#8594;</div>
            <div class="card-num-box">
              <div class="card-num-label">Final</div>
              <div class="card-num card-num-final">{_fmt_score(after)}</div>
            </div>
          </div>
          {delta_html}
        </div>"""

    if baseline_test is not None or trunk_test is not None:
        cards.append(_card("Test Set", baseline_test, trunk_test, "Primary Metric"))
    if baseline_dev is not None or trunk_dev is not None:
        cards.append(_card("Dev Set", baseline_dev, trunk_dev, "Iteration"))

    cards.append(f"""
    <div class="score-card">
      <div class="card-label">Experiments</div>
      <div class="card-stats">
        {"".join(f'<span class="stat-badge" style="background:{_STATUS_BG.get(st,"#f5f5f5")};color:{_STATUS_COLORS.get(st,"#666")}">{st}: {cnt}</span>' for st, cnt in sorted(status_counts.items()))}
      </div>
    </div>""")

    return "\n".join(cards)


def _build_chart_svg(scored: list[dict], meta: dict) -> str:
    if not scored:
        return '<div class="empty">No scored experiments yet.</div>'

    bar_h = 28
    gap = 4
    label_w = 80
    score_w = 60
    chart_area_w = 500
    total_h = len(scored) * (bar_h + gap) + 40

    baseline = meta.get("baseline_score")
    trunk = meta.get("trunk_score")

    max_score = max(n.get("score", 0) for n in scored)
    max_val = max(max_score, baseline or 0, trunk or 0, 1) * 1.1

    lines: list[str] = []
    lines.append(
        f'<svg viewBox="0 0 {label_w + chart_area_w + score_w + 20} {total_h}" '
        f'class="chart-svg">'
    )

    def _ref_line(val: float, color: str, label: str) -> str:
        x = label_w + (val / max_val) * chart_area_w
        return (
            f'<line x1="{x:.0f}" y1="0" x2="{x:.0f}" y2="{total_h}" '
            f'stroke="{color}" stroke-dasharray="6,4" stroke-width="1.5" opacity="0.7"/>'
            f'<text x="{x:.0f}" y="{total_h - 4}" fill="{color}" font-size="11" '
            f'text-anchor="middle">{label} {val:.1f}%</text>'
        )

    if baseline is not None:
        lines.append(_ref_line(baseline, "#ef4444", "Baseline"))
    if trunk is not None:
        lines.append(_ref_line(trunk, "#22c55e", "Trunk"))

    for i, node in enumerate(scored):
        y = i * (bar_h + gap) + 4
        score = node.get("score", 0)
        status = node.get("status", "unknown")
        nid = node.get("id", "?")
        bar_w = max((score / max_val) * chart_area_w, 2)
        color = _STATUS_COLORS.get(status, "#a3a3a3")

        lines.append(
            f'<text x="{label_w - 8}" y="{y + bar_h * 0.7}" '
            f'fill="#555" font-size="12" text-anchor="end" font-family="monospace">{_esc(nid)}</text>'
        )
        lines.append(
            f'<rect x="{label_w}" y="{y}" width="{bar_w:.1f}" height="{bar_h}" '
            f'rx="4" fill="{color}" opacity="0.8"/>'
        )
        lines.append(
            f'<text x="{label_w + bar_w + 6}" y="{y + bar_h * 0.7}" '
            f'fill="#333" font-size="12" font-weight="600">{score:.1f}%</text>'
        )

    lines.append("</svg>")
    return "\n".join(lines)


def _build_tree_html(nodes: dict, root_id: str, meta: dict) -> str:
    trunk_score = meta.get("trunk_score")

    def _render_node(nid: str, depth: int) -> str:
        node = nodes.get(nid, {})
        status = node.get("status", "unknown")
        score = node.get("score")
        hypothesis = node.get("hypothesis", "")
        insight = node.get("insight", "")
        result = node.get("result", "")
        code_ref = node.get("code_ref", "")
        children = node.get("children_ids", [])
        node_depth = node.get("depth", 0)

        color = _STATUS_COLORS.get(status, "#999")
        bg = _STATUS_BG.get(status, "#f9f9f9")

        score_html = ""
        if score is not None:
            delta_html = ""
            if trunk_score is not None and node_depth > 0:
                delta = score - trunk_score
                dc = "#22c55e" if delta > 0 else "#ef4444" if delta < 0 else "#888"
                sign = "+" if delta > 0 else ""
                delta_html = f' <span style="color:{dc};font-size:12px">({sign}{delta:.1f})</span>'
            score_html = f'<span class="tree-score">{score:.1f}%{delta_html}</span>'

        badge = f'<span class="status-badge" style="background:{color}">{_esc(status)}</span>'

        expanded = "open" if depth < 2 else ""

        detail_parts: list[str] = []
        if hypothesis:
            detail_parts.append(f'<div class="tree-detail-hyp">{_esc(hypothesis)}</div>')
        if insight:
            safe_insight = _esc(insight).replace("\n", "<br>")
            detail_parts.append(f'<div class="tree-detail-section"><strong>Insight:</strong><br>{safe_insight}</div>')
        if result:
            detail_parts.append(f'<div class="tree-detail-section"><strong>Result:</strong> {_esc(result)}</div>')
        if code_ref:
            detail_parts.append(f'<div class="tree-detail-section"><strong>Branch:</strong> <code>{_esc(code_ref)}</code></div>')

        detail_html = f'<div class="tree-detail">{"".join(detail_parts)}</div>' if detail_parts else ""

        children_html = ""
        if children:
            child_parts = [_render_node(cid, depth + 1) for cid in children]
            children_html = f'<div class="tree-children">{"".join(child_parts)}</div>'

        short_hyp = _shorten(hypothesis, 70)

        return f"""
        <details class="tree-node" {expanded} style="border-left: 3px solid {color}">
          <summary class="tree-summary" style="background:{bg}">
            <span class="tree-id">{_esc(nid)}</span>
            {badge}
            {score_html}
            <span class="tree-hyp-short">{_esc(short_hyp)}</span>
          </summary>
          {detail_html}
          {children_html}
        </details>"""

    return _render_node(root_id, 0)


def _build_table(scored: list[dict], meta: dict) -> str:
    if not scored:
        return '<div class="empty">No scored experiments yet.</div>'

    trunk = meta.get("trunk_score")

    rows: list[str] = []
    for i, n in enumerate(scored, 1):
        nid = n.get("id", "?")
        score = n.get("score", 0)
        status = n.get("status", "?")
        hyp = _shorten(n.get("hypothesis", ""), 60)
        insight = _shorten(n.get("insight", ""), 60)
        color = _STATUS_COLORS.get(status, "#999")
        bg = _STATUS_BG.get(status, "#fff")

        delta_html = ""
        if trunk is not None:
            d = score - trunk
            dc = "#22c55e" if d > 0 else "#ef4444" if d < 0 else "#888"
            sign = "+" if d > 0 else ""
            delta_html = f'<span style="color:{dc}">{sign}{d:.1f}</span>'

        rows.append(f"""
        <tr style="background:{bg}22">
          <td class="rank">{i}</td>
          <td class="nid"><code>{_esc(nid)}</code></td>
          <td class="score-cell"><strong>{score:.1f}%</strong></td>
          <td class="delta-cell">{delta_html}</td>
          <td><span class="status-badge" style="background:{color}">{_esc(status)}</span></td>
          <td class="hyp-cell" title="{_esc(n.get('hypothesis',''))}">{_esc(hyp)}</td>
          <td class="insight-cell" title="{_esc(n.get('insight',''))}">{_esc(insight)}</td>
        </tr>""")

    return f"""
    <table class="exp-table">
      <thead>
        <tr>
          <th>#</th><th>Node</th><th>Score</th><th>Delta</th>
          <th>Status</th><th>Hypothesis</th><th>Insight</th>
        </tr>
      </thead>
      <tbody>{"".join(rows)}</tbody>
    </table>"""


def _build_html(
    *,
    meta: dict,
    nodes: dict,
    root_id: str,
    scored: list[dict],
    status_counts: dict[str, int],
    root_node: dict,
    run_info: dict,
    tree_json: str,
) -> str:
    run_name = run_info.get("run_name", "Research Run")
    benchmark = Path(run_info.get("cwd", "")).name or "benchmark"
    duration = run_info.get("duration_seconds")
    dur_str = ""
    if duration:
        m, s = divmod(int(duration), 60)
        h, m = divmod(m, 60)
        dur_str = f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"

    score_cards = _build_score_cards(meta, status_counts)
    chart = _build_chart_svg(scored, meta)
    tree_html = _build_tree_html(nodes, root_id, meta)
    table_html = _build_table(scored, meta)

    root_insight = root_node.get("insight", "")
    root_insight_html = ""
    if root_insight:
        safe = _esc(root_insight).replace("\n", "<br>")
        root_insight_html = f'<div class="insight-content">{safe}</div>'

    info_parts: list[str] = []
    if run_info.get("cwd"):
        info_parts.append(f"CWD: {_esc(run_info['cwd'])}")
    if run_info.get("git_branch"):
        info_parts.append(f"Git: {_esc(run_info['git_branch'])} @ {_esc(run_info.get('git_commit',''))}")
    if run_info.get("trunk_branch"):
        info_parts.append(f"Trunk: {_esc(run_info['trunk_branch'])}")
    if run_info.get("config_file"):
        info_parts.append(f"Config: {_esc(run_info['config_file'])}")

    total_experiments = sum(status_counts.values())
    merged_count = status_counts.get("merged", 0)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(run_name)} — Research Dashboard</title>
<style>
:root {{
  --bg: #f8fafc;
  --surface: #ffffff;
  --text: #1e293b;
  --text2: #475569;
  --text3: #94a3b8;
  --border: #e2e8f0;
  --accent: #3b82f6;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #0f172a;
    --surface: #1e293b;
    --text: #e2e8f0;
    --text2: #94a3b8;
    --text3: #64748b;
    --border: #334155;
    --accent: #60a5fa;
  }}
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
  padding: 0;
}}
.container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
.header {{
  background: linear-gradient(135deg, #1e40af 0%, #7c3aed 100%);
  color: white;
  padding: 32px 40px;
  margin-bottom: 24px;
  border-radius: 12px;
}}
.header h1 {{ font-size: 28px; margin-bottom: 4px; font-weight: 700; }}
.header .subtitle {{ opacity: 0.85; font-size: 15px; }}
.header .meta-info {{ margin-top: 12px; font-size: 13px; opacity: 0.7; }}
.header .meta-info span {{ margin-right: 16px; }}

.section {{ margin-bottom: 28px; }}
.section-title {{
  font-size: 18px;
  font-weight: 700;
  margin-bottom: 14px;
  color: var(--text);
  display: flex;
  align-items: center;
  gap: 8px;
}}
.section-title::before {{
  content: '';
  display: inline-block;
  width: 4px;
  height: 20px;
  background: var(--accent);
  border-radius: 2px;
}}

/* Score cards */
.cards {{ display: flex; gap: 16px; flex-wrap: wrap; }}
.score-card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 20px;
  flex: 1;
  min-width: 200px;
}}
.card-label {{ font-size: 14px; font-weight: 600; color: var(--text2); margin-bottom: 2px; }}
.card-sublabel {{ font-size: 12px; color: var(--text3); margin-bottom: 8px; }}
.card-row {{ display: flex; align-items: center; gap: 12px; margin: 8px 0; }}
.card-num-box {{ text-align: center; }}
.card-num-label {{ font-size: 11px; color: var(--text3); text-transform: uppercase; letter-spacing: 0.5px; }}
.card-num {{ font-size: 28px; font-weight: 700; color: var(--text); }}
.card-num-final {{ color: var(--accent); }}
.card-arrow {{ font-size: 24px; color: var(--text3); }}
.card-delta {{ font-size: 22px; font-weight: 700; text-align: center; }}
.card-stats {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }}
.stat-badge {{
  padding: 4px 10px;
  border-radius: 12px;
  font-size: 13px;
  font-weight: 600;
}}

/* Chart */
.chart-container {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 20px;
  overflow-x: auto;
}}
.chart-svg {{ width: 100%; height: auto; }}

/* Tree */
.tree-container {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px;
}}
.tree-node {{
  margin: 4px 0 4px 12px;
  border-radius: 6px;
}}
.tree-node > .tree-node {{ margin-left: 20px; }}
.tree-summary {{
  padding: 8px 12px;
  border-radius: 5px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 14px;
  list-style: none;
}}
.tree-summary::-webkit-details-marker {{ display: none; }}
.tree-summary::before {{
  content: '\\25B6';
  font-size: 10px;
  color: var(--text3);
  transition: transform 0.15s;
}}
details[open] > .tree-summary::before {{ transform: rotate(90deg); }}
.tree-id {{ font-family: monospace; font-weight: 700; color: var(--accent); min-width: 40px; }}
.tree-score {{ font-weight: 700; font-size: 13px; }}
.tree-hyp-short {{ color: var(--text2); font-size: 13px; }}
.status-badge {{
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 600;
  color: white;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}}
.tree-detail {{
  padding: 12px 16px 12px 24px;
  font-size: 13px;
  color: var(--text2);
  line-height: 1.7;
}}
.tree-detail-hyp {{ margin-bottom: 8px; font-style: italic; }}
.tree-detail-section {{ margin-top: 8px; }}
.tree-detail-section strong {{ color: var(--text); }}
.tree-children {{ margin-left: 8px; }}

/* Table */
.table-container {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow-x: auto;
}}
.exp-table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}}
.exp-table th {{
  padding: 10px 12px;
  text-align: left;
  background: var(--bg);
  border-bottom: 2px solid var(--border);
  font-weight: 600;
  color: var(--text2);
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
}}
.exp-table th:hover {{ color: var(--accent); }}
.exp-table td {{
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}}
.exp-table .rank {{ text-align: center; color: var(--text3); width: 40px; }}
.exp-table .nid {{ white-space: nowrap; }}
.exp-table .score-cell {{ white-space: nowrap; }}
.exp-table .delta-cell {{ white-space: nowrap; text-align: right; }}
.exp-table .hyp-cell {{ max-width: 280px; }}
.exp-table .insight-cell {{ max-width: 280px; color: var(--text2); }}

/* Insight block */
.insight-block {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 20px;
}}
.insight-content {{
  font-size: 14px;
  line-height: 1.8;
  color: var(--text2);
}}
.insight-content strong {{ color: var(--text); }}

.empty {{ padding: 24px; text-align: center; color: var(--text3); }}

.footer {{
  text-align: center;
  padding: 16px;
  font-size: 12px;
  color: var(--text3);
}}

/* Tabs */
.tabs {{
  display: flex;
  gap: 4px;
  margin-bottom: 16px;
  border-bottom: 2px solid var(--border);
}}
.tab-btn {{
  padding: 8px 18px;
  border: none;
  background: none;
  cursor: pointer;
  font-size: 14px;
  font-weight: 600;
  color: var(--text3);
  border-bottom: 2px solid transparent;
  margin-bottom: -2px;
  transition: all 0.15s;
}}
.tab-btn:hover {{ color: var(--text); }}
.tab-btn.active {{
  color: var(--accent);
  border-bottom-color: var(--accent);
}}
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}

/* Filter buttons */
.filters {{
  display: flex;
  gap: 6px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}}
.filter-btn {{
  padding: 4px 12px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--surface);
  cursor: pointer;
  font-size: 12px;
  color: var(--text2);
}}
.filter-btn:hover, .filter-btn.active {{
  background: var(--accent);
  color: white;
  border-color: var(--accent);
}}

@media print {{
  body {{ background: white; }}
  .header {{ break-inside: avoid; }}
  .tabs, .filter-btn {{ display: none; }}
  .tab-content {{ display: block !important; }}
  details {{ open: true; }}
  details[open] {{ break-inside: avoid; }}
}}
</style>
</head>
<body>

<div class="container">

  <!-- Header -->
  <div class="header">
    <h1>{_esc(run_name)}</h1>
    <div class="subtitle">
      {_esc(benchmark)}
      {f' &mdash; {dur_str}' if dur_str else ''}
      &mdash; {total_experiments} experiments, {merged_count} merged
    </div>
    <div class="meta-info">
      {"".join(f'<span>{s}</span>' for s in info_parts)}
    </div>
  </div>

  <!-- Score Cards -->
  <div class="section">
    <div class="cards">{score_cards}</div>
  </div>

  <!-- Tabs: Chart / Tree / Table -->
  <div class="tabs">
    <button class="tab-btn active" onclick="switchTab('chart')">Score Chart</button>
    <button class="tab-btn" onclick="switchTab('tree')">Idea Tree</button>
    <button class="tab-btn" onclick="switchTab('table')">Experiment Table</button>
  </div>

  <div id="tab-chart" class="tab-content active">
    <div class="section">
      <div class="chart-container">{chart}</div>
    </div>
  </div>

  <div id="tab-tree" class="tab-content">
    <div class="section">
      <div class="filters">
        <button class="filter-btn active" onclick="filterTree('all')">All</button>
        <button class="filter-btn" onclick="filterTree('merged')">Merged</button>
        <button class="filter-btn" onclick="filterTree('done')">Done</button>
        <button class="filter-btn" onclick="filterTree('pending')">Pending</button>
        <button class="filter-btn" onclick="filterTree('pruned')">Pruned</button>
      </div>
      <div class="tree-container" id="idea-tree">{tree_html}</div>
    </div>
  </div>

  <div id="tab-table" class="tab-content">
    <div class="section">
      <div class="table-container">{table_html}</div>
    </div>
  </div>

  <!-- Global Insights -->
  {"" if not root_insight_html else f'''
  <div class="section">
    <div class="section-title">Global Research Insights</div>
    <div class="insight-block">{root_insight_html}</div>
  </div>
  '''}

  <div class="footer">
    Generated by Research Agent &mdash; {_esc(run_info.get("start_time", "")[:10] if run_info.get("start_time") else "")}
  </div>
</div>

<script>
function switchTab(name) {{
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  event.target.classList.add('active');
}}

function filterTree(status) {{
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  document.querySelectorAll('.tree-node').forEach(node => {{
    if (status === 'all') {{
      node.style.display = '';
      return;
    }}
    const badge = node.querySelector(':scope > .tree-summary .status-badge');
    if (badge && badge.textContent.trim().toLowerCase() === status) {{
      node.style.display = '';
      // Also show all ancestors
      let parent = node.parentElement;
      while (parent) {{
        if (parent.classList && parent.classList.contains('tree-node')) {{
          parent.style.display = '';
          parent.open = true;
        }}
        parent = parent.parentElement;
      }}
    }} else {{
      node.style.display = 'none';
    }}
  }});
}}

// Make table sortable
document.querySelectorAll('.exp-table th').forEach((th, idx) => {{
  th.addEventListener('click', () => {{
    const table = th.closest('table');
    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    const dir = th.dataset.sortDir === 'asc' ? 'desc' : 'asc';
    th.dataset.sortDir = dir;

    rows.sort((a, b) => {{
      let av = a.cells[idx].textContent.trim();
      let bv = b.cells[idx].textContent.trim();
      const an = parseFloat(av.replace('%',''));
      const bn = parseFloat(bv.replace('%',''));
      if (!isNaN(an) && !isNaN(bn)) {{
        return dir === 'asc' ? an - bn : bn - an;
      }}
      return dir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
    }});

    rows.forEach(r => tbody.appendChild(r));
  }});
}});
</script>

</body>
</html>"""
