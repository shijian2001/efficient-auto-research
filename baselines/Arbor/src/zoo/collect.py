"""The `arbor benchmark add` collection pipeline.

Orchestrates: select an acquirer → acquire materials into the global cache → scaffold a
draft pack → structurally verify it. This is the **deterministic spine** (Phase 1). The
*agent-driven* stages — Stage 0 survey (canonical source / baseline / angle) and Stage 2
baseline bring-up — need a live LLM provider and are the next sub-phase; they are invoked
only when a provider is supplied, and otherwise the pipeline produces a draft for a human
(or a later agent pass) to complete. See docs/dev/benchmark-add.md.

Isolation: this is a standalone flow built on the shared `Agent` runtime; it never wires
into the Coordinator/Executor research loop (a §2.1 correctness requirement).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .acquire import Acquired, select_acquirer
from .scaffold import scaffold_benchmark
from .verify import VerifyResult


@dataclass
class CollectResult:
    """Outcome of a collection run."""

    name: str
    acquired: Acquired | None = None
    draft_pack_dir: Path | None = None
    verify_results: list[VerifyResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ok: bool = False                       # spine completed (acquire + scaffold + verify ran)

    @property
    def pending(self) -> list[str]:
        """Human/agent steps still required before the pack can be accepted."""
        return [
            "Stage 0 survey: confirm the canonical source, the general baseline, and what Arbor edits",
            "Stage 2 bring-up: make the harvested baseline run; real eval.sh prints score: on dev/test",
            "Fill PROVENANCE (baseline implementation + contamination) and README sections",
            "Re-run `arbor benchmark verify` until green, then human-accept into arbor-zoo/",
        ]


def collect(
    spec: str,
    *,
    name: str,
    dest_root: Path,
    provider: Any | None = None,
) -> CollectResult:
    """Run the collection spine for *spec*, writing a draft pack under *dest_root*/<name>.

    *provider* is reserved for the agent-driven stages (survey / bring-up); when None,
    the deterministic spine runs and the draft is left for a human or a later agent pass.
    """
    result = CollectResult(name=name)

    acquirer = select_acquirer(spec)
    if acquirer is None:
        result.notes.append(f"no acquirer matched spec {spec!r} (expected a git URL or hf: id)")
        return result
    result.notes.append(f"acquirer: {acquirer.kind}")

    # ── Stage 1: acquire into the global cache ──────────────────────────────
    sources = acquirer.resolve(spec)
    result.acquired = acquirer.acquire(sources, name)
    result.notes.append(f"acquired into {result.acquired.cache_dir}")

    # ── Stage 0/2 (agent-driven) — next sub-phase ───────────────────────────
    # With a provider, the survey would fill the contract (canonical source / baseline /
    # angle) and bring-up would make the harvested baseline run. Until then we scaffold a
    # draft from defaults for a human (or a later agent pass) to complete.
    if provider is not None:
        result.notes.append("provider supplied, but agent stages (survey/bring-up) are not yet "
                            "implemented — scaffolding a draft for completion")

    # ── Stage 3: scaffold the draft pack (reuses the zoo scaffolder) ─────────
    draft_dir = dest_root / name
    scaffold_res = scaffold_benchmark(draft_dir, name=name, style="zoo")
    result.draft_pack_dir = draft_dir
    result.notes.append(
        f"scaffolded draft pack at {draft_dir} ({len(scaffold_res.created)} files)")

    # ── Stage 4 (structural): the zoo scaffolder already ran the verify lint ──
    result.verify_results = scaffold_res.verify
    result.ok = True
    return result
