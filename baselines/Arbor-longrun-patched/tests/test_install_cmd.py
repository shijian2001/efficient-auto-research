from __future__ import annotations

from pathlib import Path

import pytest

from arbor.cli.commands.install_cmd import (
    discover_skill_dirs,
    install_skills,
    resolve_target,
    uninstall_skills,
)


def _make_suite(root: Path, names: list[str]) -> Path:
    """Build a fake bundled-skills tree with a few arbor-* skill dirs."""
    root.mkdir(parents=True, exist_ok=True)
    # A non-skill sibling that must never be copied.
    (root / "README.md").write_text("suite readme\n", encoding="utf-8")
    for name in names:
        d = root / name
        (d / "agents").mkdir(parents=True)
        (d / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
        (d / "agents" / "openai.yaml").write_text("interface: {}\n", encoding="utf-8")
    return root


def test_discover_skill_dirs_only_returns_arbor_dirs(tmp_path: Path) -> None:
    src = _make_suite(tmp_path / "skills", ["arbor-research-agent", "arbor-agent-tools"])
    (src / "not-a-skill").mkdir()

    found = [p.name for p in discover_skill_dirs(src)]

    assert found == ["arbor-agent-tools", "arbor-research-agent"]  # sorted, prefix-filtered


def test_install_skills_copies_every_arbor_dir(tmp_path: Path) -> None:
    src = _make_suite(tmp_path / "skills", ["arbor-research-agent", "arbor-agent-tools"])
    dest = tmp_path / "out" / "skills"

    installed = install_skills(src, dest)

    assert installed == ["arbor-agent-tools", "arbor-research-agent"]
    assert (dest / "arbor-research-agent" / "SKILL.md").is_file()
    assert (dest / "arbor-agent-tools" / "agents" / "openai.yaml").is_file()
    # Non-skill siblings are not copied.
    assert not (dest / "README.md").exists()


def test_install_skills_is_idempotent_and_refreshes(tmp_path: Path) -> None:
    src = _make_suite(tmp_path / "skills", ["arbor-research-agent"])
    dest = tmp_path / "out"

    install_skills(src, dest)
    # Mutate source, reinstall: destination should be refreshed, not duplicated/stale.
    (src / "arbor-research-agent" / "SKILL.md").write_text("# updated\n", encoding="utf-8")
    installed = install_skills(src, dest)

    assert installed == ["arbor-research-agent"]
    assert (dest / "arbor-research-agent" / "SKILL.md").read_text(encoding="utf-8") == "# updated\n"


def test_uninstall_removes_only_arbor_dirs(tmp_path: Path) -> None:
    src = _make_suite(tmp_path / "skills", ["arbor-research-agent", "arbor-agent-tools"])
    dest = tmp_path / "out"
    install_skills(src, dest)
    # A user's own unrelated skill living alongside ours must survive.
    (dest / "my-own-skill").mkdir()
    (dest / "my-own-skill" / "SKILL.md").write_text("mine\n", encoding="utf-8")

    removed = uninstall_skills(dest)

    assert removed == ["arbor-agent-tools", "arbor-research-agent"]
    assert not (dest / "arbor-research-agent").exists()
    assert (dest / "my-own-skill" / "SKILL.md").is_file()


def test_resolve_target_claude_user_level(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    assert resolve_target(claude=True) == tmp_path / "home" / ".claude" / "skills"


def test_resolve_target_codex_honors_codex_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "cx"))
    assert resolve_target(codex=True) == tmp_path / "cx" / "skills"


def test_resolve_target_project_is_repo_local(tmp_path: Path) -> None:
    assert resolve_target(project=True, cwd=tmp_path / "repo") == tmp_path / "repo" / ".claude" / "skills"


def test_resolve_target_explicit_target_wins(tmp_path: Path) -> None:
    assert resolve_target(target=tmp_path / "x", claude=True) == tmp_path / "x"
