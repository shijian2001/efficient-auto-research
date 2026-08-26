"""The Arbor YAML plugin loader must preserve the eval_contract verbatim,
including the nested ``contamination`` block the contamination probe reads.
"""

from __future__ import annotations

from pathlib import Path

from arbor.plugins.base import load_plugin


def test_plugin_contamination_block_round_trips(tmp_path: Path):
    p = tmp_path / "demo.yaml"
    p.write_text(
        "name: demo\n"
        "eval_contract:\n"
        "  metric_direction: maximize\n"
        "  contamination:\n"
        "    is_public: true\n"
        "    release_date: \"2024-01-01\"\n"
        "    canaries: [\"CANARY-1\"]\n",
        encoding="utf-8",
    )
    plugin = load_plugin("demo", search_dirs=[tmp_path], strict=True)
    contamination = plugin.eval_contract["contamination"]
    assert contamination["is_public"] is True
    assert contamination["release_date"] == "2024-01-01"
    assert contamination["canaries"] == ["CANARY-1"]
