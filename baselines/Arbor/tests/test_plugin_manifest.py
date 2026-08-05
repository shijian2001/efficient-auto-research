"""Validate the Claude Code plugin + marketplace manifests.

These ship at the repo root so users can `claude plugin install arbor` to get the
skill suite and the `arbor mcp` server in one step. The tests guard against
malformed JSON and against the MCP wiring drifting from the actual CLI command.
"""

from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _load(rel: str) -> dict:
    return json.loads((_ROOT / rel).read_text(encoding="utf-8"))


def test_plugin_manifest_declares_arbor_mcp_server() -> None:
    plugin = _load(".claude-plugin/plugin.json")
    assert plugin["name"] == "arbor"
    assert plugin["license"] == "Apache-2.0"
    server = plugin["mcpServers"]["arbor"]
    # Must match the real CLI entry point: `arbor mcp`.
    assert server["command"] == "arbor"
    assert server["args"] == ["mcp"]


def test_marketplace_lists_the_plugin_at_repo_root() -> None:
    market = _load(".claude-plugin/marketplace.json")
    names = {p["name"]: p for p in market["plugins"]}
    assert "arbor" in names
    assert names["arbor"]["source"] == "./"  # the repo root is the plugin


def test_bundled_skills_are_discoverable_for_the_plugin() -> None:
    # Claude Code auto-loads a plugin's `skills/` dir; ensure the suite is there.
    skill_dirs = sorted(p.name for p in (_ROOT / "skills").iterdir() if p.is_dir() and p.name.startswith("arbor-"))
    assert "arbor-research-agent" in skill_dirs
    assert len(skill_dirs) >= 11
    for d in skill_dirs:
        assert (_ROOT / "skills" / d / "SKILL.md").is_file()
