"""Failure-preserving MLE-Bench Lite campaign aggregation."""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from ..contracts import AdapterError


@dataclass(frozen=True)
class MleSeedMetrics:
    seed: int
    total_tasks: int
    valid_rate: float
    above_median_rate: float
    any_medal_rate: float
    gold_rate: float
    failures: int
    total_tokens: int
    total_cost: float


@dataclass(frozen=True)
class MetricSummary:
    mean: float
    standard_deviation: float
    standard_error: float
    ci95_lower: float
    ci95_upper: float


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
        total_tokens += int(task_usage.get("tokens", 0))
        total_cost += float(task_usage.get("cost", 0.0))
    total = len(tasks)
    return MleSeedMetrics(
        seed=seed,
        total_tasks=total,
        valid_rate=valid / total,
        above_median_rate=above / total,
        any_medal_rate=medals / total,
        gold_rate=gold / total,
        failures=total - valid,
        total_tokens=total_tokens,
        total_cost=total_cost,
    )


def summarize(values: list[float]) -> MetricSummary:
    if not values:
        raise AdapterError("cannot summarize an empty metric")
    mean = statistics.fmean(values)
    standard_deviation = statistics.stdev(values) if len(values) > 1 else 0.0
    standard_error = standard_deviation / math.sqrt(len(values))
    margin = 1.96 * standard_error
    return MetricSummary(mean, standard_deviation, standard_error, mean - margin, mean + margin)


def aggregate_seeds(seed_metrics: list[MleSeedMetrics]) -> dict[str, Any]:
    if len(seed_metrics) < 3:
        raise AdapterError("formal MLE aggregate requires at least three seeds")
    if len({item.seed for item in seed_metrics}) != len(seed_metrics):
        raise AdapterError("MLE aggregate contains duplicate seed identities")
    task_counts = {item.total_tasks for item in seed_metrics}
    if len(task_counts) != 1:
        raise AdapterError("all MLE seeds must use the same task denominator")
    metric_names = ("valid_rate", "above_median_rate", "any_medal_rate", "gold_rate")
    return {
        "num_seeds": len(seed_metrics),
        "tasks_per_seed": task_counts.pop(),
        "seed_metrics": [asdict(item) for item in seed_metrics],
        "metrics": {
            name: asdict(summarize([float(getattr(item, name)) for item in seed_metrics]))
            for name in metric_names
        },
        "total_failures": sum(item.failures for item in seed_metrics),
        "total_tokens": sum(item.total_tokens for item in seed_metrics),
        "total_cost": sum(item.total_cost for item in seed_metrics),
        "raw_scores_averaged_across_tasks": False,
    }


__all__ = [
    "MetricSummary",
    "MleSeedMetrics",
    "aggregate_seeds",
    "calculate_seed_metrics",
    "summarize",
]
