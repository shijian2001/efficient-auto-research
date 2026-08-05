"""Global benchmark cache + manifest for the collection feature.

Acquired benchmark materials (cloned repos, downloaded datasets) live in a global,
git-ignored cache *outside* the Arbor checkout — reused across projects, never
committed. Each cached benchmark records a small ``manifest.json`` of where its
materials came from (source, commit, license, checksum) so provenance is traceable.

The cache root is ``~/.arbor/cache/benchmarks`` by default, overridable via the
``ARBOR_BENCHMARK_CACHE`` env var (used by tests and for relocating the cache).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CACHE_ENV = "ARBOR_BENCHMARK_CACHE"
MANIFEST_NAME = "manifest.json"


def cache_root() -> Path:
    """The global benchmark cache root (env override, else ``~/.arbor/cache/benchmarks``)."""
    override = os.environ.get(CACHE_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".arbor" / "cache" / "benchmarks").resolve()


def benchmark_cache_dir(name: str, *, create: bool = False) -> Path:
    """The cache directory for benchmark *name*."""
    d = cache_root() / name
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class SourceRecord:
    """One acquired source (a repo clone, a dataset download, …)."""

    kind: str                       # "git" | "hf" | "url" | …
    locator: str                    # repo URL | dataset id | file URL
    commit: str | None = None       # pinned git commit / dataset revision
    license: str | None = None
    checksum: str | None = None     # sha256 of the key artifact, when applicable

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class Manifest:
    """What was fetched for a cached benchmark, and from where."""

    name: str
    sources: list[SourceRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "sources": [s.to_dict() for s in self.sources]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Manifest":
        return cls(
            name=data.get("name", ""),
            sources=[SourceRecord(**s) for s in data.get("sources", [])],
        )


def load_manifest(cache_dir: Path) -> Manifest | None:
    """Read ``manifest.json`` from *cache_dir*, or None if absent/unreadable."""
    path = cache_dir / MANIFEST_NAME
    if not path.exists():
        return None
    try:
        return Manifest.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def save_manifest(cache_dir: Path, manifest: Manifest) -> None:
    """Write ``manifest.json`` into *cache_dir* (created if needed)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def record_source(cache_dir: Path, name: str, source: SourceRecord) -> Manifest:
    """Append *source* to the benchmark's manifest (creating it if needed) and persist."""
    manifest = load_manifest(cache_dir) or Manifest(name=name)
    manifest.name = manifest.name or name
    manifest.sources.append(source)
    save_manifest(cache_dir, manifest)
    return manifest


def sha256_file(path: Path) -> str:
    """Hex sha256 of a file (for checksumming a key downloaded artifact)."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
