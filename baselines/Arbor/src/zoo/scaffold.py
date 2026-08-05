"""``arbor.zoo.scaffold`` — create the reference benchmark folder structure.

Deterministic, keyless file writer. It writes the *measurement plumbing* (an eval
entrypoint, a dev/test split layout, an editable ``solution.py`` placeholder) and, for
the ``zoo`` style, a natural-language ``README.md`` (the task description Arbor reads at
intake) + a ``PROVENANCE.md`` card for humans. It never writes the solution logic.

The format is documentation-first: the README is plain prose (no YAML manifest), so the
scaffolded files are starting points for a human/agent to fill in. Idempotent and
non-destructive: an existing file is recorded under ``skipped`` and never overwritten.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .pack import find_eval_entrypoint
from .verify import VerifyResult, verify_pack

_STYLES = ("light", "zoo")
_ENTRYPOINTS = ("eval.py", "eval.sh")
_DIRECTIONS = ("maximize", "minimize")
_SPLIT_KINDS = ("seed_range", "path")


@dataclass
class ScaffoldResult:
    """Outcome of one scaffold call."""

    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    verify: list[VerifyResult] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)


# ── templates ──────────────────────────────────────────────────────────────

_SOLUTION = '''"""solution.py — the editable baseline (Arbor's edit surface).

Replace this with a working baseline. It must be correct first; Arbor then
optimizes it to improve the score. Keep the entry-point signature stable —
``eval.py`` calls into it.
"""

from __future__ import annotations


def solve(problem):
    """TODO: return a solution for *problem* (the simplest correct baseline)."""
    raise NotImplementedError("fill in the baseline solver")
'''

_EVAL_SH = '''#!/usr/bin/env bash
# eval.sh — PROTECTED wrapper. Do not edit during a research run.
#   bash eval.sh dev    # Arbor iterates here
#   bash eval.sh test   # held-out gate
# Prints a single "score: <float>" line.
set -euo pipefail
SPLIT="${1:-dev}"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONHASHSEED=0
HERE="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON:-python3}"
exec "$PYTHON" "$HERE/eval.py" --split "$SPLIT"
'''

_PROVENANCE = """# Provenance

This card is for humans. Fill in every section; a maintainer reads it before the
benchmark is accepted.

## Source

Where the benchmark comes from — paper, repo, or competition, with a link, and how the
data was collected or generated.

## Setup & environment

Hardware (CPU / GPU), Python version, install command, env vars, and any API keys,
downloads, or services the user must provision. State whether it is offline. License of
the code and data, and whether the data may be redistributed.

## Baseline

How the shipped baseline works, and what score it tends to produce. **Results vary** by
user, hardware, and (for API tasks) model — note the range you saw rather than a single
fixed number.

## Contamination assessment

**Mandatory.** Could the test split be in a model's pre-training data? Is the held-out
split truly disjoint from dev? Explain why a high score reflects real capability.

## Caveats

Known limitations — hardware sensitivity, metric noise, scope.
"""


def _eval_py(split_kind: str) -> str:
    """Render a protected eval.py stub for the declared split kind.

    The stub catches NotImplementedError and prints ``score: 0.0`` so the metric is
    parseable before the baseline is filled in.
    """
    if split_kind == "path":
        head = "from pathlib import Path\n"
        body = (
            "    data_dir = Path(__file__).parent / \"data\" / split\n"
            "    _instances = sorted(p for p in data_dir.glob(\"*\") if p.is_file())\n"
            "    raise NotImplementedError(\"score solution.solve over the split's instances\")\n"
        )
    else:
        head = "DEV_SEED_BASE = 1000\nTEST_SEED_BASE = 9000\nN_INSTANCES = 3\n"
        body = (
            "    base = DEV_SEED_BASE if split == \"dev\" else TEST_SEED_BASE\n"
            "    _seeds = [base + i for i in range(N_INSTANCES)]\n"
            "    raise NotImplementedError(\"compute the score from solution.solve\")\n"
        )
    return (
        '"""eval.py — PROTECTED evaluation harness. Do not edit during a research run.\n\n'
        "Prints exactly one line ``score: <float>`` for ``--split dev|test``. dev and test\n"
        "must use disjoint data; describe the split in README / PROVENANCE.\n"
        '"""\n\n'
        "from __future__ import annotations\n\n"
        "import argparse\n"
        f"{head}\n\n"
        "def evaluate(split: str) -> float:\n"
        f"{body}\n\n"
        "def main() -> None:\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument(\"--split\", choices=[\"dev\", \"test\"], default=\"dev\")\n"
        "    args = parser.parse_args()\n"
        "    try:\n"
        "        score = evaluate(args.split)\n"
        "    except NotImplementedError:\n"
        "        score = 0.0\n"
        "    print(f\"score: {score:.4f}\")\n\n\n"
        "if __name__ == \"__main__\":\n"
        "    main()\n"
    )


def _readme(name: str, direction: str, edit: list[str], eval_entrypoint: str,
            split_kind: str) -> str:
    """Render a natural-language README — the task description Arbor reads at intake."""
    better = "higher is better" if direction == "maximize" else "lower is better"
    editable = ", ".join(f"`{e}`" for e in edit)
    run = ("bash eval.sh dev\nbash eval.sh test"
           if eval_entrypoint == "eval.sh"
           else "python eval.py --split dev\npython eval.py --split test")
    split_note = (
        "dev and test use **disjoint seed ranges** so the held-out split is never the data "
        "you tune on." if split_kind == "seed_range" else
        "dev and test live in **separate folders** (`data/dev/`, `data/test/`); the test "
        "split is held out."
    )
    return (
        f"# {name}\n\n"
        "One-line summary of the benchmark.\n\n"
        "## The task\n"
        "TODO — what the task is and what a solution looks like.\n\n"
        "## Metric\n"
        f"Running the eval prints one `score:` line; **{better}**.\n\n"
        "## What Arbor may edit\n"
        f"{editable} is the editable baseline. The eval harness and any ground-truth / data "
        "are off-limits.\n\n"
        "## Dev / test\n"
        f"{split_note}\n\n"
        "## Run it\n"
        "```bash\n"
        f"{run}\n"
        "```\n\n"
        "See [`PROVENANCE.md`](PROVENANCE.md) for source, setup, and the baseline write-up.\n"
    )


def _next_steps(style: str) -> list[str]:
    steps = [
        "Fill in `solution.py` with the simplest correct baseline.",
        "Implement `evaluate()` in `eval.py` to score `solution.solve`.",
    ]
    if style == "zoo":
        steps.append("Complete `README.md` (the task for Arbor) and `PROVENANCE.md` (for humans).")
        steps.append("Run `arbor benchmark verify <dir>` until it exits 0.")
    return steps


def scaffold_benchmark(
    target: Path,
    *,
    name: str,
    metric_direction: str = "maximize",
    style: str = "light",
    split_kind: str = "seed_range",
    eval_entrypoint: str = "eval.py",
    edit: list[str] | None = None,
) -> ScaffoldResult:
    """Scaffold the Arbor reference folder under *target*. See module docstring."""
    if style not in _STYLES:
        raise ValueError(f"style must be one of {_STYLES}, got {style!r}")
    if metric_direction not in _DIRECTIONS:
        raise ValueError(f"metric_direction must be one of {_DIRECTIONS}")
    if eval_entrypoint not in _ENTRYPOINTS:
        raise ValueError(f"eval_entrypoint must be one of {_ENTRYPOINTS}")
    if split_kind not in _SPLIT_KINDS:
        raise ValueError(f"split_kind must be one of {_SPLIT_KINDS}")

    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    edit = edit or ["solution.py"]
    res = ScaffoldResult()

    def write(rel: str, content: str) -> None:
        p = target / rel
        if p.exists():
            res.skipped.append(rel)
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        # Force LF: a CRLF-translated eval.sh shebang is a broken interpreter on Unix.
        p.write_text(content, encoding="utf-8", newline="\n")
        res.created.append(rel)

    existing = find_eval_entrypoint(target)
    if existing is None:
        write("eval.py", _eval_py(split_kind))
        if eval_entrypoint == "eval.sh":
            write("eval.sh", _EVAL_SH)
    else:
        res.skipped.append(existing)

    if split_kind == "path":
        # Visible placeholder instances (globs skip dotfiles, so .gitkeep wouldn't show).
        write("data/dev/example_001.txt", "# replace with a real dev instance\n")
        write("data/test/example_001.txt", "# replace with a real held-out test instance\n")

    write("solution.py", _SOLUTION)

    if style == "zoo":
        write("README.md", _readme(name, metric_direction, edit, eval_entrypoint, split_kind))
        write("PROVENANCE.md", _PROVENANCE)
        write("requirements.txt", "# add runtime dependencies here\n")
        res.verify = verify_pack(target)

    res.next_steps = _next_steps(style)
    return res
