"""Generate a non-promoting FML upstream protocol review candidate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..protocol import sha256_file
from ..registry import AGENTS
from ..registry import ROOT
from .agents import adapter_registry_digest
from .protocol import FMLProtocol, SHARED_ADAPTER_FILES
from .task import FMLTaskSpec


UPSTREAM_SCORE_RANGES_BY_TASK = {
    "Causality_causalml": {"best": 0.0, "worst": "baseline"},
    "Causality_gcastle": {"best": 0.0, "worst": "baseline"},
    "Continual_Learning_continual_learning": {"best": 1.0, "worst": 0.0},
    "Continual_Learning_pycil": {"best": 1.0, "worst": 0.0},
    "Data_Efficiency_easyfsl": {"best": 1.0, "worst": 0.0},
    "Data_Efficiency_usb": {"best": 1.0, "worst": 0.0},
    "Fairness_and_Bias_aif360": {"best": 0.0, "worst": 1.0},
    "Fairness_fairlearn": {"best": 0.0, "worst": 1.0},
    "Federated_Learning_PFLlib": {"best": 1.0, "worst": 0.0},
    "Generalization_domainbed": {"best": 1.0, "worst": 0.0},
    "Generalization_domainbed_officehome": {"best": 1.0, "worst": 0.0},
    "Privacy_opacus": {"best": 1.0, "worst": 0.0},
    "Privacy_privacymeter": {"best": 0.0, "worst": 0.5},
    "Representation_Learning_lightly": {"best": 1.0, "worst": 0.0},
    "Representation_Learning_solo_learn": {"best": 1.0, "worst": 0.0},
    "Robustness_and_Reliability_art": {"best": 1.0, "worst": 0.0},
    "Robustness_openood": {"best": 1.0, "worst": 0.0},
    "Unlearning_open_unlearning": {"best": 0.0, "worst": "baseline"},
}


def build_review_candidate(
    *, upstream_root: Path, wall_clock_seconds: int = 172800
) -> FMLProtocol:
    upstream_root = upstream_root.resolve()
    commit = __import__("subprocess").run(
        ["git", "-C", str(upstream_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    task_paths = tuple(sorted((upstream_root / "configs/tasks").glob("*.yaml")))
    ranges: dict[str, dict[str, float | str]] = {}
    for path in task_paths:
        payload = __import__("yaml").safe_load(path.read_text(encoding="utf-8"))
        ranges[path.stem] = dict(
            UPSTREAM_SCORE_RANGES_BY_TASK[str(payload["benchmark"]["name"])]
        )
    evaluator_files = {
        relative: sha256_file(upstream_root / relative)
        for relative in (
            "benchmark/executor.py",
            "benchmark/runner.py",
            "benchmark/utils.py",
            "compute_agent_metrics.py",
        )
    }
    protocol = FMLProtocol(
        schema_version=2,
        benchmark_id="fml-bench",
        protocol_version="fml-bench-pinned-upstream-review-v1",
        upstream_root=upstream_root,
        upstream_commit=commit,
        task_config_paths=task_paths,
        task_config_digests={path.name: sha256_file(path) for path in task_paths},
        evaluator_files=evaluator_files,
        shared_adapter_files={
            relative: sha256_file(ROOT / relative) for relative in SHARED_ADAPTER_FILES
        },
        agent_adapter_digest=adapter_registry_digest(),
        internal_round_policy="one native Agent search run per task; development evaluations are ordered immutable proposal records",
        internal_proposal_policy="at most 50 shared host development evaluations; failures remain explicit; final held-out evaluation is one-shot",
        wall_clock_seconds=wall_clock_seconds,
        outer_run_ids=(0,),
        gpu_type="NVIDIA H100",
        gpus_per_evaluation=1,
        max_concurrent_evaluations=1,
        agent_adapter_ids=tuple(AGENTS),
        max_agent_steps=50,
        max_evaluator_calls=50,
        allowed_dependency_policy="use only each task's frozen upstream Conda environment; no Agent-installed dependencies",
        task_score_ranges=ranges,
        primary_metric_name="<PRIMARY_METRIC_REQUIRES_REVIEW>",
        formal_status="review-required",
    )
    protocol.validate(formal=False)
    return protocol


def audit_report(protocol: FMLProtocol) -> dict[str, Any]:
    dirty = __import__("subprocess").run(
        ["git", "-C", str(protocol.upstream_root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return {
        "benchmark_id": protocol.benchmark_id,
        "upstream_commit": protocol.upstream_commit,
        "upstream_dirty": bool(dirty),
        "task_count": len(protocol.task_config_paths),
        "task_ids": [path.stem for path in protocol.task_config_paths],
        "metrics_available": ["average_improvement", "win_rate"],
        "primary_metric_frozen": protocol.primary_metric_name in {"average_improvement", "win_rate"},
        "formal_status": protocol.formal_status,
        "protocol_frozen": protocol.protocol_frozen,
        "automatic_promotion": False,
        "known_differences": [
            "registered seven-Agent adapters replace upstream baseline AgentRegistry",
            "shared host evaluator owns development/final evaluation and immutable evidence",
            "outer repetitions are distinct from Agent proposals and upstream internal rounds",
        ],
    }


def rendered_instruction_audit(
    task: FMLTaskSpec, *, development_command: str
) -> dict[str, Any]:
    from .agents import FML_AGENT_ADAPTERS

    prompts = {
        agent_id: task.render(development_command=development_command)
        for agent_id in FML_AGENT_ADAPTERS
    }
    digests = {
        agent_id: __import__("hashlib").sha256(prompt.encode("utf-8")).hexdigest()
        for agent_id, prompt in prompts.items()
    }
    return {
        "schema_version": 1,
        "task_id": task.task_id,
        "canonical_task_digest": task.digest,
        "rendered_prompt_digests": digests,
        "semantic_payload_identical": len(set(prompts.values())) == 1,
        "hidden_agent_specific_task_advice": False,
    }


__all__ = [
    "UPSTREAM_SCORE_RANGES_BY_TASK",
    "audit_report",
    "build_review_candidate",
    "rendered_instruction_audit",
]
