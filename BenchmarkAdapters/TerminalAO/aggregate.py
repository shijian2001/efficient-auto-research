"""Three-seed Terminal AO score aggregation with fixed held-out denominator."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from ..contracts import AdapterError
from ..MLEBenchLite.aggregate import summarize
from ..protocol import BenchmarkMode
from ..records import RunStatus
from ..registry import AGENTS
from .protocol import TerminalAOProtocol


@dataclass(frozen=True)
class TerminalAOSeedMetrics:
    seed: int
    pass_rate: float
    passed: int
    failed: int
    errors: int
    missing_rewards: int
    score_valid: bool
    wall_clock_seconds: float
    total_tokens: int
    total_cost: float


def aggregate_terminal_ao(
    *,
    protocol: TerminalAOProtocol,
    campaign_dir: Path,
    agent: str,
) -> dict[str, Any]:
    protocol.validate()
    if agent not in AGENTS:
        raise AdapterError(f"unknown baseline agent: {agent}")
    metrics: list[TerminalAOSeedMetrics] = []
    failures: list[dict[str, object]] = []
    for seed in protocol.seeds:
        result_path = campaign_dir / agent / f"seed-{seed}" / "result.json"
        payload: dict[str, Any] | None = None
        if result_path.is_file():
            try:
                candidate = json.loads(result_path.read_text(encoding="utf-8"))
                if (
                    candidate.get("protocol_digest") == protocol.digest
                    and candidate.get("mode") == BenchmarkMode.TERMINAL_AO.value
                    and candidate.get("agent") == agent
                    and int(candidate.get("seed")) == seed
                    and candidate.get("status") == RunStatus.COMPLETED.value
                    and candidate.get("score_valid") is True
                ):
                    payload = candidate
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = None
        run_metrics = payload.get("metrics", {}) if payload else {}
        pass_rate = float(payload["score"]) if payload else 0.0
        if not 0.0 <= pass_rate <= 1.0:
            raise AdapterError(f"Terminal AO pass rate is outside [0, 1] for seed {seed}")
        passed = int(run_metrics.get("passed", 0)) if payload else 0
        failed = int(run_metrics.get("failed", 53)) if payload else 53
        if passed + failed != 53:
            raise AdapterError(f"Terminal AO seed {seed} does not use the frozen 53-task denominator")
        tokens = sum(int(value) for value in payload.get("tokens", {}).values()) if payload else 0
        cost = sum(float(value) for value in payload.get("cost", {}).values()) if payload else 0.0
        metrics.append(
            TerminalAOSeedMetrics(
                seed=seed,
                pass_rate=pass_rate,
                passed=passed,
                failed=failed,
                errors=int(run_metrics.get("errors", 0)) if payload else 0,
                missing_rewards=int(run_metrics.get("missing_rewards", 53)) if payload else 53,
                score_valid=payload is not None,
                wall_clock_seconds=float(payload.get("wall_clock_seconds", 0.0)) if payload else 0.0,
                total_tokens=tokens,
                total_cost=cost,
            )
        )
        if payload is None:
            failures.append(
                {
                    "seed": seed,
                    "result_path": str(result_path),
                    "reason": "missing or invalid completed Terminal AO result; counted as zero",
                }
            )
    if len(metrics) < 3:
        raise AdapterError("formal Terminal AO aggregate requires at least three seeds")
    summary = summarize([item.pass_rate for item in metrics])
    return {
        "protocol_id": protocol.protocol_id,
        "protocol_digest": protocol.digest,
        "mode": BenchmarkMode.TERMINAL_AO.value,
        "agent": agent,
        "primary_metric": "held_out_53_pass_rate_avg_at_3",
        "num_seeds": len(metrics),
        "tasks_per_seed": 53,
        "metrics": {"held_out_53_pass_rate": asdict(summary)},
        "seed_metrics": [asdict(item) for item in metrics],
        "invalid_or_missing_seeds": len(failures),
        "failures": failures,
        "total_tokens": sum(item.total_tokens for item in metrics),
        "total_cost": sum(item.total_cost for item in metrics),
        "direct_89_scores_included": False,
        "composite_with_mle": False,
    }


__all__ = ["TerminalAOSeedMetrics", "aggregate_terminal_ao"]
