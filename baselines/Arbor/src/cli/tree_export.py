"""Render a recording as a standalone, shareable hypothesis-tree replay page.

The terminal dashboard and this HTML are the same idea — a projection of the
event bus — in two surfaces. ``arbor replay`` paints the tree in your terminal;
``arbor replay --html`` writes a single self-contained file that animates the
same run in a browser: branches grow, pruned ones grey out, merged ones glow,
and a timeline scrubs the whole search. No server, no external scripts, no
network — so it drops straight into an issue, a slide, or a tweet.

The page is a template with the recording's events + meta injected inline; all
the tree logic lives in the template's vanilla JS (it re-folds the events exactly
as ``run_state.py`` does), so this module stays a thin data-injection layer.
"""

from __future__ import annotations

import json
from pathlib import Path

from .replay import Recording

_TEMPLATE = Path(__file__).resolve().parent / "assets" / "tree_template.html"
_SENTINEL = "__ARBOR_DATA__"


def build_tree_html(rec: Recording) -> str:
    """Return the self-contained HTML for ``rec`` as a string."""
    payload = {
        "meta": {
            "run_name": rec.run_name,
            "model": rec.model,
            "task": rec.task,
            "metric_direction": rec.metric_direction,
            "baseline_score": rec.baseline_score,
            "trunk_score": rec.trunk_score,
        },
        "events": [{"ts": ts, "type": etype, "data": data} for ts, etype, data in rec.events],
    }
    # ensure_ascii keeps the file ASCII-safe; the </script> guard prevents any
    # string in the data from prematurely closing the inline <script> block.
    blob = json.dumps(payload, ensure_ascii=True).replace("</", "<\\/")
    template = _TEMPLATE.read_text(encoding="utf-8")
    if _SENTINEL not in template:
        raise RuntimeError(f"tree template is missing the {_SENTINEL} placeholder")
    return template.replace(_SENTINEL, blob)


def write_tree_html(rec: Recording, output: Path) -> Path:
    """Write the replay page for ``rec`` to ``output`` and return the path."""
    output = Path(output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_tree_html(rec), encoding="utf-8")
    return output


def default_html_path(rec: Recording) -> Path:
    """Where ``--html`` writes when no path is given.

    Inside the session dir when we know it (keeps artifacts together), else a
    named file in the current directory.
    """
    if rec.session_dir is not None:
        return Path(rec.session_dir) / "arbor-tree.html"
    return Path.cwd() / f"arbor-tree-{rec.run_name}.html"
