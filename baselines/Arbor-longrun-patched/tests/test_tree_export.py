"""Tests for the standalone tree-replay HTML export (``arbor replay --html``).

The tree logic lives in the template's JS; the Python side just injects the
recording's events + meta. So we assert the injection is well-formed and safe,
and that the demo round-trips into a self-contained page.
"""

from __future__ import annotations

import json
import re

from arbor.cli.replay import demo_recording
from arbor.cli.tree_export import build_tree_html, default_html_path, write_tree_html


def _extract_payload(html: str) -> dict:
    """Pull the injected ``const DATA = {...};`` blob back out and parse it."""
    m = re.search(r"const DATA = (\{.*?\});", html, re.DOTALL)
    assert m, "DATA blob not found in rendered HTML"
    return json.loads(m.group(1).replace("<\\/", "</"))


def test_build_tree_html_injects_events_and_meta() -> None:
    rec = demo_recording()
    html = build_tree_html(rec)

    assert "__ARBOR_DATA__" not in html          # placeholder fully replaced
    assert "<!DOCTYPE html>" in html
    assert "<svg" in html and "stateAt" in html  # the renderer is present

    payload = _extract_payload(html)
    assert payload["meta"]["metric_direction"] == "maximize"
    assert payload["meta"]["baseline_score"] == 1.0
    assert len(payload["events"]) == rec.event_count
    # The merged frontier the JS will compute is present in the data.
    assert any(e["type"] == "idea.merged" for e in payload["events"])


def test_html_is_self_contained() -> None:
    """No network deps — the file must work opened straight from disk."""
    html = build_tree_html(demo_recording())
    assert "http://" not in html.replace("http://www.w3.org/2000/svg", "")
    assert "https://" not in html
    assert "<script src" not in html             # no external scripts
    assert "<link" not in html                   # no external styles


def test_script_tag_is_not_broken_by_payload() -> None:
    """A stray '</script>' inside event text must be escaped, not emitted raw."""
    html = build_tree_html(demo_recording())
    body = html.split("const DATA = ", 1)[1].split(";", 1)[0]
    assert "</script>" not in body               # escaped to <\/script>


def test_write_tree_html_round_trips(tmp_path) -> None:
    rec = demo_recording()
    out = write_tree_html(rec, tmp_path / "tree.html")
    assert out.exists()
    assert out.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_default_html_path_uses_session_dir(tmp_path) -> None:
    rec = demo_recording()
    rec.session_dir = tmp_path
    assert default_html_path(rec) == tmp_path / "arbor-tree.html"
    rec.session_dir = None
    assert default_html_path(rec).name == "arbor-tree-demo.html"
