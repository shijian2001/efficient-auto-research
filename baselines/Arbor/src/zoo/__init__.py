"""``arbor.zoo`` — the benchmark format, its verifier, scaffolder, and collection spine.

A benchmark is a self-contained directory under ``arbor-zoo/``: a natural-language README
(the task description Arbor reads), a PROVENANCE card for humans, a runnable baseline (one
or more code files), and a protected eval entrypoint. The format is documentation-first —
no machine manifest. This package provides pack discovery (:mod:`~arbor.zoo.pack`), the
``arbor benchmark verify`` lint (:mod:`~arbor.zoo.verify`), the scaffolder
(:mod:`~arbor.zoo.scaffold`), and the ``arbor benchmark add`` collection spine
(:mod:`~arbor.zoo.acquire` / :mod:`~arbor.zoo.cache` / :mod:`~arbor.zoo.collect`).
See ``docs/zoo.md``.
"""

from __future__ import annotations

from .acquire import Acquired, Acquirer, GitRepoAcquirer, Sources, select_acquirer
from .agent_stages import BringupResult, DiscoveryResult, bringup, discover, real_agent_runner
from .ask_tool import ConsoleAskUserTool
from .cache import Manifest, benchmark_cache_dir, cache_root
from .collect import CollectResult, collect
from .pack import (
    EVAL_ENTRYPOINTS,
    PackSummary,
    discover_packs,
    find_eval_entrypoint,
    is_pack_dir,
)
from .scaffold import ScaffoldResult, scaffold_benchmark
from .verify import VerifyResult, verify_pack

__all__ = [
    "EVAL_ENTRYPOINTS",
    "Acquired",
    "Acquirer",
    "BringupResult",
    "CollectResult",
    "ConsoleAskUserTool",
    "DiscoveryResult",
    "GitRepoAcquirer",
    "Manifest",
    "PackSummary",
    "ScaffoldResult",
    "Sources",
    "VerifyResult",
    "benchmark_cache_dir",
    "bringup",
    "cache_root",
    "collect",
    "discover",
    "discover_packs",
    "find_eval_entrypoint",
    "is_pack_dir",
    "real_agent_runner",
    "scaffold_benchmark",
    "select_acquirer",
    "verify_pack",
]
