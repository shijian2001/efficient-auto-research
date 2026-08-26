"""arbor-research-agent stays the public entry point; the setup-intake phase
gains the scaffold + persisted-contract behavior."""

from __future__ import annotations

from pathlib import Path

_SKILLS = Path(__file__).resolve().parent.parent / "skills"
_ENTRY = _SKILLS / "arbor-research-agent" / "SKILL.md"
_SETUP = _SKILLS / "arbor-agent-setup-intake" / "SKILL.md"


def test_no_competing_entry_point_skill_exists() -> None:
    # Scaffolding must live in the setup phase, not a new public entry point.
    assert not (_SKILLS / "arbor-prepare-benchmark").exists()


def test_entry_point_is_still_arbor_research_agent() -> None:
    text = _ENTRY.read_text(encoding="utf-8")
    assert "name: arbor-research-agent" in text
    # It delegates the not-eval-ready case to the setup phase's scaffolder.
    assert "scaffold" in text.lower()


def test_setup_intake_drives_scaffold_and_persists_contract() -> None:
    text = _SETUP.read_text(encoding="utf-8")
    assert "scaffold_benchmark" in text
    assert "ARBOR_CONTRACT.md" in text
    assert "research_config.yaml" in text
