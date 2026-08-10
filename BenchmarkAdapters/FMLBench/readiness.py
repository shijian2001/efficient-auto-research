"""Evidence-based readiness for the seven formal FML Agent adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..contracts import AdapterError
from ..registry import AGENTS
from ..thin_registry import (
    AGENT_VARIANTS,
    THIN_CLASSIFICATIONS,
    UPSTREAM_REVISIONS,
    git_source_state,
)
from .agents import FML_AGENT_ADAPTERS, get_fml_agent_adapter
from .protocol import FMLProtocol


def collect_fml_readiness(
    *, protocol_path: Path | None = None, formal_evidence_root: Path | None = None
) -> dict[str, Any]:
    agents: dict[str, Any] = {}
    for agent_id in AGENTS:
        adapter_class = FML_AGENT_ADAPTERS.get(agent_id)
        classification = THIN_CLASSIFICATIONS.get(agent_id, {}).get("fml-bench")
        adapter_defined = adapter_class is not None and classification != "unsupported"
        report = adapter_class().validate_installation()
        source_commit, source_dirty = git_source_state(AGENTS[agent_id].install_path)
        reviewed_original_source = (
            agent_id not in UPSTREAM_REVISIONS
            or (
                source_commit == UPSTREAM_REVISIONS[agent_id]
                and source_dirty is False
            )
        )
        agents[agent_id] = {
            "adapter_defined": adapter_defined,
            "adapter_class": None if adapter_class is None else adapter_class.__name__,
            "native_entrypoint": None if adapter_class is None else adapter_class.native_entrypoint,
            "thin_adapter_classification": classification,
            "registered_variants": sorted(
                key
                for key, variant in AGENT_VARIANTS.items()
                if variant.base_agent == agent_id and "fml-bench" in variant.benchmarks
            ),
            "reviewed_original_source": reviewed_original_source,
            "launcher_smoke_ready": bool(
                report.ready and adapter_defined and reviewed_original_source
            ),
            "installation": report.to_dict(),
            "formal_scored": False,
        }
    protocol_frozen = False
    protocol_detail = "no reviewed protocol path supplied"
    if protocol_path is not None:
        try:
            protocol = FMLProtocol.load(protocol_path, formal=False)
            protocol_frozen = protocol.protocol_frozen
            protocol_detail = (
                "frozen" if protocol_frozen else f"{protocol.formal_status}; primary={protocol.primary_metric_name}"
            )
        except AdapterError as exc:
            protocol_detail = f"invalid: {exc}"
    formal_scored_agents: list[str] = []
    if formal_evidence_root is not None and formal_evidence_root.is_dir():
        for agent_id in AGENTS:
            evidence = formal_evidence_root / agent_id / "formal-aggregate.json"
            if evidence.is_file():
                formal_scored_agents.append(agent_id)
                agents[agent_id]["formal_scored"] = True
    adapter_count = sum(bool(value["adapter_defined"]) for value in agents.values())
    launcher_count = sum(bool(value["launcher_smoke_ready"]) for value in agents.values())
    return {
        "schema_version": 1,
        "benchmark_id": "fml-bench",
        "agents": agents,
        "adapter_defined": {"count": adapter_count, "total": len(AGENTS), "complete": adapter_count == len(AGENTS)},
        "launcher_smoke_ready": {"count": launcher_count, "total": len(AGENTS), "complete": launcher_count == len(AGENTS)},
        "protocol_frozen": protocol_frozen,
        "protocol_detail": protocol_detail,
        "formal_preflight_ready": protocol_frozen and launcher_count == len(AGENTS),
        "formal_scored": len(formal_scored_agents) == len(AGENTS),
        "formal_scored_agents": formal_scored_agents,
    }


__all__ = ["collect_fml_readiness"]
