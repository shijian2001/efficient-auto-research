"""Static thin-adapter capabilities for the three reviewed baseline Agents."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .contracts import AdapterError, UnsupportedAdapterError
from .registry import AGENTS


BENCHMARK_IDS = (
    "mle-bench-lite",
    "terminal-bench-ao",
    "autoresearch-architecture",
    "optimizer-design",
    "fml-bench",
)

THIN_CLASSIFICATIONS = {
    "arbor": {
        "mle-bench-lite": "unsupported",
        "terminal-bench-ao": "official-extension-thin",
        "autoresearch-architecture": "official-extension-thin",
        "optimizer-design": "official-extension-thin",
        "fml-bench": "official-extension-thin",
    },
    "ai-scientist": {
        "mle-bench-lite": "native-thin",
        "terminal-bench-ao": "unsupported",
        "autoresearch-architecture": "unsupported",
        "optimizer-design": "unsupported",
        "fml-bench": "unsupported",
    },
    "ml-master-2": {
        "mle-bench-lite": "native-thin",
        "terminal-bench-ao": "unsupported",
        "autoresearch-architecture": "unsupported",
        "optimizer-design": "unsupported",
        "fml-bench": "unsupported",
    },
}

UPSTREAM_REVISIONS = {
    "arbor": "65ffcc8fdf23a64a781940e6a3cfb6369d6d887e",
    "ai-scientist": "db8cce71cf9668a4946d1eace72290d9e3376164",
    "ml-master-2": "36a52bc6c42a6b9fd710a41c52f3c3bb948b9ac9",
}


@dataclass(frozen=True)
class AgentVariantSpec:
    key: str
    base_agent: str
    display_name: str
    benchmarks: tuple[str, ...]
    implementation: str


AGENT_VARIANTS = {
    "arbor-benchmark-patched": AgentVariantSpec(
        key="arbor-benchmark-patched",
        base_agent="arbor",
        display_name="Arbor benchmark-patched variant",
        benchmarks=BENCHMARK_IDS,
        implementation="patched Arbor coordinator/executor and benchmark MLE runtime",
    ),
    "ai-scientist-terminal-variant": AgentVariantSpec(
        key="ai-scientist-terminal-variant",
        base_agent="ai-scientist",
        display_name="AiScientist terminal Subagent variant",
        benchmarks=("terminal-bench-ao", "fml-bench"),
        implementation="TerminalTaskSubagent with benchmark-specific terminal tools",
    ),
    "ai-scientist-architecture-variant": AgentVariantSpec(
        key="ai-scientist-architecture-variant",
        base_agent="ai-scientist",
        display_name="AiScientist architecture-design Subagent variant",
        benchmarks=("autoresearch-architecture", "optimizer-design"),
        implementation="ArchitectureDesignSubagent with benchmark candidate tools",
    ),
    "ml-master-autoresearch-variant": AgentVariantSpec(
        key="ml-master-autoresearch-variant",
        base_agent="ml-master-2",
        display_name="ML-Master benchmark-defined staged workflow variant",
        benchmarks=(
            "terminal-bench-ao",
            "autoresearch-architecture",
            "optimizer-design",
            "fml-bench",
        ),
        implementation="benchmark-defined BaseAgent.run stage orchestration",
    ),
}

CELL_CLASSIFICATIONS = {
    agent: {
        benchmark: (
            "patched-variant"
            if classification == "unsupported"
            and any(
                variant.base_agent == agent and benchmark in variant.benchmarks
                for variant in AGENT_VARIANTS.values()
            )
            else classification
        )
        for benchmark, classification in classifications.items()
    }
    for agent, classifications in THIN_CLASSIFICATIONS.items()
}

VARIANT_BACKENDS = {
    "arbor-benchmark-patched": {
        "mle-bench-lite": "native-docker",
        "terminal-bench-ao": "native-arbor-repository",
        "autoresearch-architecture": "native-arbor-coordinator",
        "optimizer-design": "optimizer-design-arbor-coordinator",
        "fml-bench": "native-arbor-coordinator",
    },
    "ai-scientist-terminal-variant": {
        "terminal-bench-ao": "native-ai-scientist-subagent",
        "fml-bench": "native-ai-scientist-subagent",
    },
    "ai-scientist-architecture-variant": {
        "autoresearch-architecture": "native-ai-scientist-subagent",
        "optimizer-design": "optimizer-design-ai-scientist-subagent",
    },
    "ml-master-autoresearch-variant": {
        "terminal-bench-ao": "native-ml-master-2-repository",
        "autoresearch-architecture": "native-ml-master-2-workflow",
        "optimizer-design": "optimizer-design-ml-master-2-workflow",
        "fml-bench": "native-ml-master-2-workflow",
    },
}


def variant_name(agent_variant: str | None) -> str | None:
    value = (agent_variant or "").strip()
    if not value or value.lower() == "default":
        return None
    return value.partition("@")[0]


def selected_variant(
    agent: str,
    benchmark_id: str,
    agent_variant: str | None,
) -> AgentVariantSpec | None:
    raw_variant = (agent_variant or "").strip()
    name = variant_name(agent_variant)
    if name is None:
        return None
    if name == agent:
        _, separator, revision = raw_variant.partition("@")
        expected = UPSTREAM_REVISIONS.get(agent)
        if expected is not None and (not separator or revision.lower() != expected):
            raise AdapterError(
                f"original {agent} variant must be pinned as {agent}@{expected}"
            )
        return None
    if name not in AGENT_VARIANTS:
        if agent in THIN_CLASSIFICATIONS:
            raise AdapterError(
                f"unregistered {agent} variant: {name}; use {agent}@<commit> for "
                "the reviewed original or select an explicit registered variant"
            )
        return None
    variant = AGENT_VARIANTS[name]
    if variant.base_agent != agent:
        raise AdapterError(
            f"Agent variant {name} belongs to {variant.base_agent}, not {agent}"
        )
    if benchmark_id not in variant.benchmarks:
        raise UnsupportedAdapterError(
            f"Agent variant {name} does not support {benchmark_id}"
        )
    return variant


def require_thin_support(
    agent: str,
    benchmark_id: str,
    agent_variant: str | None = None,
) -> AgentVariantSpec | None:
    variant = selected_variant(agent, benchmark_id, agent_variant)
    if variant is not None:
        return variant
    classification = THIN_CLASSIFICATIONS.get(agent, {}).get(benchmark_id)
    if classification == "unsupported":
        raise UnsupportedAdapterError(
            f"{agent} has no official thin adapter for {benchmark_id}; "
            "select an explicit registered variant if one is acceptable"
        )
    return None


def backend_identity(agent: str, benchmark_id: str, agent_variant: str | None) -> str:
    variant = selected_variant(agent, benchmark_id, agent_variant)
    if variant is not None:
        return VARIANT_BACKENDS[variant.key][benchmark_id]
    require_thin_support(agent, benchmark_id, agent_variant)
    spec = AGENTS[agent]
    return {
        "mle-bench-lite": spec.mle_backend,
        "terminal-bench-ao": spec.terminal_ao_backend,
        "autoresearch-architecture": spec.autoresearch_backend,
        "optimizer-design": spec.optimizer_design_backend,
        "fml-bench": spec.autoresearch_backend,
    }[benchmark_id]


def git_source_state(path: Path) -> tuple[str | None, bool | None]:
    git_config: list[str] = []
    excludes_file = os.environ.get("BENCHMARK_ADAPTERS_GIT_EXCLUDES_FILE")
    if excludes_file:
        git_config.extend(("-c", f"core.excludesFile={excludes_file}"))
    commit = subprocess.run(
        ["git", *git_config, "-C", str(path.resolve()), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    dirty = subprocess.run(
        [
            "git",
            *git_config,
            "-C",
            str(path.resolve()),
            "status",
            "--porcelain",
            "--untracked-files=normal",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if commit.returncode or dirty.returncode:
        return None, None
    return commit.stdout.strip(), bool(dirty.stdout.strip())


def require_clean_upstream_source(agent: str) -> str:
    try:
        expected = UPSTREAM_REVISIONS[agent]
    except KeyError as exc:
        raise AdapterError(f"no reviewed upstream source identity for {agent}") from exc
    commit, dirty = git_source_state(AGENTS[agent].install_path)
    if commit != expected or dirty is not False:
        raise UnsupportedAdapterError(
            f"{agent} original thin adapter requires clean upstream {expected}; "
            f"found commit={commit} dirty={dirty}. Patched sources never satisfy the original ID."
        )
    return expected


__all__ = [
    "AGENT_VARIANTS",
    "BENCHMARK_IDS",
    "CELL_CLASSIFICATIONS",
    "THIN_CLASSIFICATIONS",
    "UPSTREAM_REVISIONS",
    "VARIANT_BACKENDS",
    "AgentVariantSpec",
    "backend_identity",
    "git_source_state",
    "require_clean_upstream_source",
    "require_thin_support",
    "selected_variant",
    "variant_name",
]
