"""``arbor benchmark verify`` — a light structural lint for a benchmark folder.

The zoo format is documentation-first and natural-language: the README describes the
task for Arbor in plain prose (no machine manifest), and PROVENANCE is the human card.
So ``verify`` is a completeness check, **not** a correctness gate — it does not run the
eval (a baseline's score is not universal: it varies by user, hardware, and model). It
checks the pieces are present: a README, a PROVENANCE card with its sections, and an eval
entrypoint. Whether dev/test are truly held out and what the baseline really does are
stated in PROVENANCE prose and judged by a human.

:func:`verify_pack` returns one :class:`VerifyResult` per check; the CLI exits non-zero
if any has ``status == "fail"``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .pack import find_eval_entrypoint

# Required PROVENANCE.md headings (keyword → human label), verified by presence.
PROVENANCE_HEADINGS = (
    ("source", "Source"),
    ("setup", "Setup & environment"),
    ("baseline", "Baseline"),
    ("contamination", "Contamination assessment"),
    ("caveat", "Caveats"),
)


@dataclass
class VerifyResult:
    """One check outcome. Same shape as ``arbor.cli.preflight.CheckResult`` but defined
    here to keep ``arbor.zoo`` free of any dependency on ``arbor.cli``."""

    name: str
    status: str  # "pass" | "warn" | "fail"
    message: str
    hint: str | None = None


def _headings(md_text: str) -> list[str]:
    return [ln.lstrip("#").strip().lower()
            for ln in md_text.splitlines() if ln.lstrip().startswith("#")]


def _check_files_present(pack_dir: Path) -> VerifyResult:
    """README, PROVENANCE, and an eval entrypoint exist (the eval is not run)."""
    missing = [f for f in ("README.md", "PROVENANCE.md") if not (pack_dir / f).exists()]
    if find_eval_entrypoint(pack_dir) is None:
        missing.append("eval.sh or eval.py")
    if missing:
        return VerifyResult(
            "files-present", "fail",
            f"missing: {', '.join(missing)}",
            "a benchmark needs a README, a PROVENANCE card, and an eval entrypoint",
        )
    return VerifyResult("files-present", "pass", "README, PROVENANCE, and eval entrypoint present")


def _check_readme(pack_dir: Path) -> VerifyResult:
    """The README (Arbor's task description) exists and has some content."""
    readme = pack_dir / "README.md"
    if not readme.exists():
        return VerifyResult("readme", "fail", "README.md missing")
    if len(readme.read_text(encoding="utf-8").strip()) < 20:
        return VerifyResult("readme", "fail", "README.md is essentially empty",
                            "describe the task, the metric, and what Arbor may edit")
    return VerifyResult("readme", "pass", "README present")


def _check_provenance(pack_dir: Path) -> VerifyResult:
    """PROVENANCE.md exists and has its required headings (the human card)."""
    prov = pack_dir / "PROVENANCE.md"
    if not prov.exists():
        return VerifyResult("provenance", "fail", "PROVENANCE.md missing")
    heads = _headings(prov.read_text(encoding="utf-8"))
    missing = [label for kw, label in PROVENANCE_HEADINGS if not any(kw in h for h in heads)]
    if missing:
        return VerifyResult("provenance", "fail",
                            f"PROVENANCE missing sections: {', '.join(missing)}",
                            "the contamination assessment in particular is required")
    return VerifyResult("provenance", "pass", "all required PROVENANCE sections present")


_CHECKS: tuple[Callable[[Path], VerifyResult], ...] = (
    _check_files_present,
    _check_readme,
    _check_provenance,
)


def verify_pack(pack_dir: Path) -> list[VerifyResult]:
    """Structurally verify the benchmark in *pack_dir* (does not run the eval)."""
    pack_dir = pack_dir.resolve()

    def _safe(check: Callable[[Path], VerifyResult]) -> VerifyResult:
        try:
            return check(pack_dir)
        except Exception as exc:  # noqa: BLE001 — a check bug must not crash verify
            return VerifyResult(check.__name__, "fail", f"check raised: {exc}")

    return [_safe(c) for c in _CHECKS]
