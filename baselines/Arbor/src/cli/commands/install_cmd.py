"""``arbor install`` / ``arbor uninstall`` — manage the bundled Agent Skill suite.

Arbor ships an 11-directory *Agent Skill suite* (``skills/arbor-*``) that lets a
host coding agent (Claude Code, Codex, …) run the Arbor research workflow using
*its own* model — no Arbor API key, no separate runtime. Historically users had
to copy those directories by hand (see ``skills/README.md``). These commands
automate that: ``arbor install`` discovers the suite bundled with the installed
package and copies it into the right harness skills directory; ``arbor
uninstall`` removes exactly the directories Arbor owns and nothing else.

Design notes
------------
* **Idempotent.** Re-running ``install`` refreshes each ``arbor-*`` directory in
  place (remove-then-copy) so an upgrade never leaves stale files behind.
* **Namespaced + safe.** We only ever touch directories whose names start with
  ``arbor-``. A user's own unrelated skills living in the same target directory
  are never read, copied, or removed.
* **No LLM, no key.** This command performs pure filesystem work; it is part of
  Arbor's keyless coding-agent integration path.

The public, unit-tested surface is the small pure-function layer
(:func:`bundled_skills_root`, :func:`discover_skill_dirs`, :func:`resolve_target`,
:func:`install_skills`, :func:`uninstall_skills`); the Typer commands at the
bottom are thin wrappers that add flag parsing and human-readable output.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import typer

from ..._app import APP_NAME

# Every skill directory in the suite is namespaced with this prefix. It is the
# single rule that makes install/uninstall safe to run in a shared directory.
SKILL_PREFIX = "arbor-"

# Environment override for the bundled-skills location. Primarily a test and
# packaging-debug hook; normal installs never need to set it.
SKILLS_DIR_ENV = "ARBOR_SKILLS_DIR"


def bundled_skills_root() -> Path:
    """Locate the ``skills/`` suite that ships with this Arbor install.

    Resolution order (first hit wins):

    1. ``$ARBOR_SKILLS_DIR`` — explicit override (tests / packaging debug).
    2. ``<arbor package>/skills_suite`` — the location the wheel ships the suite
       to (see ``pyproject.toml`` ``package-dir`` mapping).
    3. ``<repo root>/skills`` — an editable / source checkout, where the suite
       lives one level above the ``arbor`` package (``src/``).

    Raises:
        FileNotFoundError: if no bundled suite can be found in any location.
    """
    override = os.environ.get(SKILLS_DIR_ENV)
    if override:
        p = Path(override).expanduser()
        if p.is_dir():
            return p

    import arbor  # local import: avoids a hard import cycle at module load time

    pkg_dir = Path(arbor.__file__).resolve().parent

    # Wheel layout: the suite is shipped *inside* the package as ``skills_suite``.
    packaged = pkg_dir / "skills_suite"
    if packaged.is_dir():
        return packaged

    # Editable / source layout: ``src/__init__.py`` → ``src/`` → repo root.
    dev = pkg_dir.parent / "skills"
    if dev.is_dir():
        return dev

    raise FileNotFoundError(
        "Could not locate the bundled Arbor skill suite. Looked under "
        f"${SKILLS_DIR_ENV}, {packaged}, and {dev}."
    )


def discover_skill_dirs(root: Path) -> list[Path]:
    """Return the ``arbor-*`` skill directories directly under *root*, sorted.

    Only immediate subdirectories whose names start with :data:`SKILL_PREFIX`
    are returned; loose files (e.g. the suite ``README.md``) and unrelated
    directories are ignored. Sorting gives deterministic, stable output for both
    user messaging and tests.
    """
    if not root.is_dir():
        return []
    return sorted(
        (p for p in root.iterdir() if p.is_dir() and p.name.startswith(SKILL_PREFIX)),
        key=lambda p: p.name,
    )


def resolve_target(
    *,
    claude: bool = False,
    codex: bool = False,
    project: bool = False,
    target: Path | str | None = None,
    cwd: Path | None = None,
) -> Path:
    """Compute the destination skills directory from the selected install mode.

    Precedence (highest first):

    * ``target`` — an explicit directory, used verbatim.
    * ``project`` — ``<cwd>/.claude/skills`` (repo-local Claude Code skills).
    * ``codex``   — ``${CODEX_HOME:-~/.codex}/skills``.
    * ``claude`` / default — ``~/.claude/skills`` (user-level Claude Code skills).

    The Claude user-level directory is the default because it is the most common
    setup (the suite available across every project).
    """
    if target is not None:
        return Path(target).expanduser()
    if project:
        base = Path(cwd) if cwd is not None else Path.cwd()
        return base / ".claude" / "skills"
    if codex:
        codex_home = os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
        return Path(codex_home).expanduser() / "skills"
    # ``claude`` flag and the no-flag default both land here.
    return Path.home() / ".claude" / "skills"


def install_skills(src_root: Path, dest: Path) -> list[str]:
    """Copy every ``arbor-*`` skill dir from *src_root* into *dest*.

    Existing ``arbor-*`` directories at the destination are removed first so the
    copy is a clean refresh (no orphaned files survive an upgrade). Returns the
    sorted list of skill directory names that were installed.
    """
    skills = discover_skill_dirs(src_root)
    dest.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    for skill in skills:
        out = dest / skill.name
        if out.exists():
            shutil.rmtree(out)
        shutil.copytree(skill, out)
        installed.append(skill.name)
    return installed


def uninstall_skills(dest: Path) -> list[str]:
    """Remove only the ``arbor-*`` skill dirs from *dest*; leave everything else.

    Returns the sorted list of skill directory names that were removed (empty if
    none were present).
    """
    removed: list[str] = []
    for skill in discover_skill_dirs(dest):
        shutil.rmtree(skill)
        removed.append(skill.name)
    return removed


# ── Typer command wrappers ───────────────────────────────────────────────────


def install_command(
    claude: bool = typer.Option(
        False, "--claude", help="Install into ~/.claude/skills (user-level)."
    ),
    codex: bool = typer.Option(
        False, "--codex", help="Install into ${CODEX_HOME:-~/.codex}/skills."
    ),
    project: bool = typer.Option(
        False, "--project", help="Install into <cwd>/.claude/skills (repo-local)."
    ),
    target: Path | None = typer.Option(
        None, "--target", help="Install into an explicit directory.", show_default=False
    ),
) -> None:
    """Install the bundled Arbor skill suite into a coding-agent skills directory."""
    selected = [name for name, on in (("claude", claude), ("codex", codex), ("project", project)) if on]
    if len(selected) > 1 or (selected and target is not None):
        typer.secho(
            "error: choose at most one of --claude / --codex / --project / --target.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        src_root = bundled_skills_root()
    except FileNotFoundError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    dest = resolve_target(claude=claude, codex=codex, project=project, target=target)
    if not (claude or codex or project or target is not None):
        # Surface the default explicitly so the user is never surprised about
        # where their skills landed.
        typer.secho(f"No target flag given — defaulting to {dest}", fg=typer.colors.YELLOW)

    installed = install_skills(src_root, dest)
    if not installed:
        typer.secho(
            f"error: no '{SKILL_PREFIX}*' skills found under {src_root}.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    typer.secho(f"\nInstalled {len(installed)} {APP_NAME} skills into {dest}:", fg=typer.colors.GREEN, bold=True)
    for name in installed:
        typer.echo(f"  ✓ {name}")
    typer.secho(
        "\nRestart your coding agent, then run `/arbor-research-agent <your task>`.",
        fg=typer.colors.CYAN,
    )


def uninstall_command(
    claude: bool = typer.Option(False, "--claude", help="Uninstall from ~/.claude/skills."),
    codex: bool = typer.Option(False, "--codex", help="Uninstall from ${CODEX_HOME:-~/.codex}/skills."),
    project: bool = typer.Option(False, "--project", help="Uninstall from <cwd>/.claude/skills."),
    target: Path | None = typer.Option(
        None, "--target", help="Uninstall from an explicit directory.", show_default=False
    ),
) -> None:
    """Remove the Arbor skill suite from a coding-agent skills directory."""
    dest = resolve_target(claude=claude, codex=codex, project=project, target=target)
    removed = uninstall_skills(dest)
    if not removed:
        typer.secho(f"No {APP_NAME} skills found in {dest}.", fg=typer.colors.YELLOW)
        return
    typer.secho(f"Removed {len(removed)} {APP_NAME} skills from {dest}:", fg=typer.colors.GREEN, bold=True)
    for name in removed:
        typer.echo(f"  ✓ {name}")
