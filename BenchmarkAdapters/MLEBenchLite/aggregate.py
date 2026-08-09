"""Failure-preserving MLE-Bench Lite campaign aggregation."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from ..contracts import AdapterError
from ..formal_contract import repetition_summary


@dataclass(frozen=True)
class MleSeedMetrics:
    seed: int
    total_tasks: int
    valid_rate: float
    above_median_rate: float
    any_medal_rate: float
    gold_rate: float
    failures: int
    total_tokens: int | None
    total_cost: float | None


def _flag(report: Mapping[str, Any] | None, name: str) -> bool:
    return bool(report and report.get(name))


def calculate_seed_metrics(
    *,
    seed: int,
    task_ids: Iterable[str],
    reports: Mapping[str, Mapping[str, Any] | None],
    usage: Mapping[str, Mapping[str, float | int]] | None = None,
) -> MleSeedMetrics:
    tasks = tuple(task_ids)
    if not tasks or len(set(tasks)) != len(tasks):
        raise AdapterError("aggregation task set must be non-empty and unique")
    unknown = set(reports) - set(tasks)
    if unknown:
        raise AdapterError(f"grading reports contain tasks outside protocol: {sorted(unknown)}")
    usage = usage or {}
    valid = above = medals = gold = 0
    total_tokens = 0
    total_cost = 0.0
    tokens_complete = True
    cost_complete = True
    raw_scores: dict[str, float] = {}
    for task_id in tasks:
        report = reports.get(task_id)
        if _flag(report, "valid_submission") and report.get("score") is not None:
            valid += 1
            raw_scores[task_id] = float(report["score"])
        above += int(_flag(report, "above_median"))
        medals += int(_flag(report, "any_medal"))
        gold += int(_flag(report, "gold_medal"))
        task_usage = usage.get(task_id, {})
        if task_usage.get("tokens") is None:
            tokens_complete = False
        else:
            total_tokens += int(task_usage["tokens"])
        if task_usage.get("cost") is None:
            cost_complete = False
        else:
            total_cost += float(task_usage["cost"])
    total = len(tasks)
    return MleSeedMetrics(
        seed=seed,
        total_tasks=total,
        valid_rate=valid / total,
        above_median_rate=above / total,
        any_medal_rate=medals / total,
        gold_rate=gold / total,
        failures=total - valid,
        total_tokens=total_tokens if tokens_complete else None,
        total_cost=total_cost if cost_complete else None,
    )


def aggregate_seeds(
    seed_metrics: list[MleSeedMetrics],
    *,
    outer_repetitions: int | None = None,
) -> dict[str, Any]:
    repetitions = outer_repetitions or len(seed_metrics)
    if repetitions not in {1, 3} or len(seed_metrics) != repetitions:
        raise AdapterError("formal MLE aggregate requires all configured N=1 or N=3 outer runs")
    if len({item.seed for item in seed_metrics}) != len(seed_metrics):
        raise AdapterError("MLE aggregate contains duplicate seed identities")
    task_counts = {item.total_tasks for item in seed_metrics}
    if len(task_counts) != 1:
        raise AdapterError("all MLE seeds must use the same task denominator")
    metric_names = ("valid_rate", "above_median_rate", "any_medal_rate", "gold_rate")
    return {
        "outer_repetitions": repetitions,
        "reporting_label": "single_run" if repetitions == 1 else "avg_at_3",
        "num_seeds": len(seed_metrics),
        "tasks_per_seed": task_counts.pop(),
        "seed_metrics": [asdict(item) for item in seed_metrics],
        "metrics": {
            name: repetition_summary(
                [float(getattr(item, name)) for item in seed_metrics],
                outer_repetitions=repetitions,
            )
            for name in metric_names
        },
        "total_failures": sum(item.failures for item in seed_metrics),
        "total_tokens": (
            sum(int(item.total_tokens) for item in seed_metrics)
            if all(item.total_tokens is not None for item in seed_metrics)
            else None
        ),
        "total_cost": (
            sum(float(item.total_cost) for item in seed_metrics)
            if all(item.total_cost is not None for item in seed_metrics)
            else None
        ),
        "raw_scores_averaged_across_tasks": False,
    }


__all__ = [
    "MleSeedMetrics",
    "aggregate_seeds",
    "calculate_seed_metrics",
    "summarize",
]
