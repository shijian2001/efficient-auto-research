"""Acquirers — fetch a benchmark's materials into the global cache.

The collection pipeline is modality-agnostic after acquisition: an :class:`Acquirer`
turns a user spec (a repo URL, a HF dataset id) into a cached, provenance-recorded set
of materials, and everything downstream (scaffold → bring-up → verify) is shared. v1
ships :class:`GitRepoAcquirer` (fully implemented) and :class:`HFDatasetAcquirer`
(needs the optional ``huggingface_hub`` dependency).

Network/auth/large-file handling lives here; keep it bounded and fail loudly.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .cache import Manifest, SourceRecord, benchmark_cache_dir, record_source


@dataclass
class Sources:
    """What Stage 0 (the survey) resolved a spec to. See benchmark-add.md."""

    kind: str                    # "git" | "hf"
    locator: str                 # canonical repo URL+commit | HF dataset id
    license: str | None = None
    baseline_ref: str = ""       # harvested baseline impl (de-risks bring-up)
    angle: str = ""              # the locked angle: what's editable vs frozen
    commit: str | None = None    # pinned commit / revision
    notes: str = ""              # metric, floor/general/SOTA points, feasibility


@dataclass
class Acquired:
    """What Stage 1 produced: cached materials + the provenance manifest."""

    cache_dir: Path
    manifest: Manifest
    materials_dir: Path          # where the clone/download landed inside cache_dir


@runtime_checkable
class Acquirer(Protocol):
    kind: str

    def matches(self, spec: str) -> bool: ...
    def resolve(self, spec: str) -> Sources: ...
    def acquire(self, sources: Sources, name: str) -> Acquired: ...
    def bringup_recipe(self) -> str: ...


def _run(cmd: list[str], cwd: Path | None = None) -> str:
    """Run a subprocess, raising RuntimeError with output on failure."""
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"command failed ({' '.join(cmd)}):\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout.strip()


_GIT_SPEC = re.compile(r"^(https?://|git@|file://).*|.*\.git$|^[\w.-]+/[\w.-]+$")
_HF_SPEC = re.compile(r"^hf:.+|^datasets/.+")


@dataclass
class GitRepoAcquirer:
    """Clone a git repo (optionally at a pinned commit) into the cache."""

    kind: str = "git"

    def matches(self, spec: str) -> bool:
        s = spec.strip()
        if _HF_SPEC.match(s):
            return False
        return bool(_GIT_SPEC.match(s)) or Path(s).expanduser().is_dir()

    def resolve(self, spec: str) -> Sources:
        # "<url>@<commit>" pins a commit.
        locator, _, commit = spec.strip().partition("@")
        return Sources(kind="git", locator=locator, commit=commit or None,
                       notes="git repo (confirm canonical source + commit at Stage 0)")

    def acquire(self, sources: Sources, name: str) -> Acquired:
        cache_dir = benchmark_cache_dir(name, create=True)
        materials = cache_dir / "repo"
        if materials.exists():
            # already cloned — reuse the cache (idempotent)
            pass
        else:
            depth = [] if sources.commit else ["--depth", "1"]
            _run(["git", "clone", *depth, sources.locator, str(materials)])
            if sources.commit:
                _run(["git", "fetch", "--depth", "1", "origin", sources.commit], cwd=materials)
                _run(["git", "checkout", sources.commit], cwd=materials)
        head = None
        try:
            head = _run(["git", "rev-parse", "HEAD"], cwd=materials)
        except RuntimeError:
            pass
        manifest = record_source(
            cache_dir, name,
            SourceRecord(kind="git", locator=sources.locator,
                         commit=sources.commit or head, license=sources.license),
        )
        return Acquired(cache_dir=cache_dir, manifest=manifest, materials_dir=materials)

    def bringup_recipe(self) -> str:
        return (
            "The repo ships a runnable baseline + eval. Make it run (install deps), then "
            "wrap a clean `eval.sh dev|test` that prints `score:` on a held-out split. "
            "Harvest the existing baseline — do not invent one."
        )


@dataclass
class HFDatasetAcquirer:
    """Fetch a HuggingFace dataset into the cache (needs ``huggingface_hub``)."""

    kind: str = "hf"

    def matches(self, spec: str) -> bool:
        return bool(_HF_SPEC.match(spec.strip()))

    def resolve(self, spec: str) -> Sources:
        locator = spec.strip().removeprefix("hf:").removeprefix("datasets/")
        return Sources(kind="hf", locator=locator,
                       notes="HF dataset (build an API-judged scorer + dev/test split at Stage 2)")

    def acquire(self, sources: Sources, name: str) -> Acquired:
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise RuntimeError(
                "HF acquisition needs `huggingface_hub` — `pip install huggingface_hub`"
            ) from exc
        cache_dir = benchmark_cache_dir(name, create=True)
        materials = Path(snapshot_download(repo_id=sources.locator, repo_type="dataset",
                                           local_dir=str(cache_dir / "data")))
        manifest = record_source(
            cache_dir, name,
            SourceRecord(kind="hf", locator=sources.locator,
                         commit=sources.commit, license=sources.license),
        )
        return Acquired(cache_dir=cache_dir, manifest=manifest, materials_dir=materials)

    def bringup_recipe(self) -> str:
        return (
            "The dataset has no harness. Construct an API-judged scorer that prints `score:` "
            "and a disjoint dev/test split; harvest the general baseline (e.g. a DSPy CoT "
            "pipeline) rather than inventing one."
        )


# Registry, in match-precedence order (HF before git so `datasets/x` wins).
ACQUIRERS: list[Acquirer] = [HFDatasetAcquirer(), GitRepoAcquirer()]


def select_acquirer(spec: str) -> Acquirer | None:
    """Return the first acquirer that matches *spec*, or None."""
    for a in ACQUIRERS:
        if a.matches(spec):
            return a
    return None
