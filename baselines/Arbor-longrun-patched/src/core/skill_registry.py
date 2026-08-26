"""Skill registry: discovers markdown-based skills from built-in and project dirs.

A skill is a markdown file with YAML frontmatter:

    ---
    name: my_skill
    description: one-line summary used in tool description
    when_to_apply: optional hint for the LLM
    ---

    # body...

The body (everything after the closing `---`) is what `LoadSkill` returns to
the LLM. The frontmatter is parsed for registry metadata only.

Discovery order (later overrides earlier on name collision):
  1. Built-in: <package>/skills/*.md
  2. Project:  <cwd>/.arbor/skills/*.md
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class Skill:
    name: str
    description: str
    when_to_apply: str
    body: str
    source_path: str
    source: str = "custom"


class SkillRegistry:
    """In-memory registry of available skills, loaded once at startup."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def load_dir(self, dir_path: str, *, source: str = "custom") -> int:
        """Load all *.md files from a directory. Returns count loaded."""
        if not os.path.isdir(dir_path):
            return 0
        count = 0
        for entry in sorted(os.listdir(dir_path)):
            if not entry.endswith(".md"):
                continue
            path = os.path.join(dir_path, entry)
            try:
                skill = _parse_skill_file(path, source=source)
            except Exception as exc:  # noqa: BLE001
                log.warning("skill_registry: failed to parse %s: %s", path, exc)
                continue
            if skill.name in self._skills:
                log.info(
                    "skill_registry: %s overrides built-in (from %s)",
                    skill.name,
                    skill.source_path,
                )
            self._skills[skill.name] = skill
            count += 1
        return count

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def names(self) -> list[str]:
        return sorted(self._skills.keys())

    def summaries(self) -> list[tuple[str, str]]:
        """Return [(name, description), ...] sorted by name, for tool description."""
        return [(s.name, s.description) for s in sorted(self._skills.values(), key=lambda s: s.name)]

    def summaries_with_source(self) -> list[tuple[str, str]]:
        """Return [(name, "source · description"), ...] sorted by name."""
        return [
            (s.name, f"{s.source} · {s.description}")
            for s in sorted(self._skills.values(), key=lambda s: s.name)
        ]

    def __len__(self) -> int:
        return len(self._skills)


def _parse_skill_file(path: str, *, source: str = "custom") -> Skill:
    """Parse a single skill markdown file. Minimal YAML frontmatter parser
    (name/description/when_to_apply only — no PyYAML dependency)."""
    text = Path(path).read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("missing frontmatter (file must start with '---')")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError("unterminated frontmatter (no closing '---')")
    fm_block = text[4:end]
    body = text[end + 5:].lstrip("\n")

    meta: dict[str, str] = {}
    for line in fm_block.splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        meta[key.strip()] = val.strip().strip('"').strip("'")

    name = meta.get("name") or Path(path).stem
    description = meta.get("description", "(no description)")
    when_to_apply = meta.get("when_to_apply", "")
    return Skill(
        name=name,
        description=description,
        when_to_apply=when_to_apply,
        body=body,
        source_path=path,
        source=source,
    )


def build_default_registry(cwd: str, *, disabled: list[str] | set[str] | tuple[str, ...] = ()) -> SkillRegistry:
    """Build a registry with built-in skills + project overrides."""
    registry = SkillRegistry()
    # Built-in: <package_root>/skills/
    pkg_skills = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")
    n_builtin = registry.load_dir(pkg_skills, source="built-in")
    # Project override
    project_skills = os.path.join(cwd, ".arbor", "skills")
    n_project = registry.load_dir(project_skills, source="project")
    for name in disabled:
        registry._skills.pop(str(name), None)
    log.info(
        "skill_registry: loaded %d built-in + %d project skills (total %d)",
        n_builtin,
        n_project,
        len(registry),
    )
    return registry
