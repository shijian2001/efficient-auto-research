"""Pack discovery for the ``arbor-zoo`` benchmark format.

A *benchmark* is a directory under ``arbor-zoo/`` holding a self-contained task:
a natural-language ``README.md`` (what the task is, which score to optimize, what
Arbor may edit — read by Arbor at intake), a ``PROVENANCE.md`` card for humans, a
runnable **baseline** (one or more code files), and a protected eval entrypoint
(``eval.sh``/``eval.py``) that prints one ``score: <float>`` line.

The format is **documentation-first**: there is no machine manifest. Discovery and
verification work by convention — see :mod:`arbor.zoo.verify` and ``docs/zoo.md``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Eval entrypoints recognised by convention, in preference order.
EVAL_ENTRYPOINTS = ("eval.sh", "eval.py")


@dataclass(frozen=True)
class PackSummary:
    """Lightweight index entry for ``arbor benchmark list`` / the zoo README."""

    name: str
    description: str
    path: str


def find_eval_entrypoint(pack_dir: Path) -> str | None:
    """Return the eval entrypoint filename in *pack_dir*, or None if absent."""
    for name in EVAL_ENTRYPOINTS:
        if (pack_dir / name).exists():
            return name
    return None


def is_pack_dir(path: Path) -> bool:
    """True when *path* looks like a benchmark: a non-scaffold dir with a README
    and an eval entrypoint."""
    if not path.is_dir() or path.name.startswith((".", "_")):
        return False
    return (path / "README.md").exists() and find_eval_entrypoint(path) is not None


def _readme_description(pack_dir: Path) -> str:
    """First non-heading, non-blank line of the README — a one-line description.

    Tolerates (and skips) a legacy leading ``---``-fenced block if one is present.
    """
    readme = pack_dir / "README.md"
    if not readme.exists():
        return "(no description)"
    lines = readme.read_text(encoding="utf-8").splitlines()
    i = 0
    if lines and lines[0].strip() == "---":
        for j in range(1, len(lines)):
            if lines[j].strip() == "---":
                i = j + 1
                break
    for line in lines[i:]:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return "(no description)"


def discover_packs(zoo_dir: Path) -> list[PackSummary]:
    """Return every benchmark under *zoo_dir*, skipping ``_``-prefixed scaffolds."""
    out: list[PackSummary] = []
    if not zoo_dir.exists() or not zoo_dir.is_dir():
        return out
    for child in sorted(zoo_dir.iterdir()):
        if not is_pack_dir(child):
            continue
        out.append(PackSummary(
            name=child.name,
            description=_readme_description(child),
            path=str(child),
        ))
    return sorted(out, key=lambda p: p.name)
