"""Security regression tests for prior-experience discovery and composition."""

from __future__ import annotations

import pytest

from arbor.recall import compose_from_sessions, list_experiences


def test_compose_from_sessions_rejects_path_traversal(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "EXPERIENCE.md").write_text("- secret", encoding="utf-8")
    project = tmp_path / "project"
    (project / ".arbor" / "sessions").mkdir(parents=True)

    assert compose_from_sessions(str(project), ["../../../outside"]) == ""


def test_experience_discovery_rejects_symlinked_session(tmp_path):
    project = tmp_path / "project"
    sessions = project / ".arbor" / "sessions"
    sessions.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "EXPERIENCE.md").write_text(
        "description: leaked\n- secret",
        encoding="utf-8",
    )
    link = sessions / "evil"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - platform permission edge
        pytest.skip(f"symlink unavailable: {exc}")

    assert list_experiences(str(project)) == []
    assert compose_from_sessions(str(project), ["evil"]) == ""