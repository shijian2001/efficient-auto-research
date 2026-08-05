#!/usr/bin/env python3
"""Plot hourly best-score curves for efficient and MLEvolve runs."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path


ROOT = Path("/mnt/sdc/shijianwang/efficient-agent-research")
COMPS = [
    "chaii-hindi-and-tamil-question-answering",
    "jigsaw-toxic-comment-classification-challenge",
    "mlsp-2013-birds",
]
SHORT = {
    "chaii-hindi-and-tamil-question-answering": "chaii",
    "jigsaw-toxic-comment-classification-challenge": "jigsaw",
    "mlsp-2013-birds": "mlsp-birds",
}
MLEVOLVE_RUNS = {
    "chaii-hindi-and-tamil-question-answering": ROOT / "baselines/MLEvolve/runs/20260601_142018_docker_chaii-hindi-and-tamil-question-answering",
    "jigsaw-toxic-comment-classification-challenge": ROOT / "baselines/MLEvolve/runs/20260601_142019_docker_jigsaw-toxic-comment-classification-challenge",
    "mlsp-2013-birds": ROOT / "baselines/MLEvolve/runs/20260601_142019_docker_mlsp-2013-birds",
}


def best_curve(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    points = sorted((h, v) for h, v in points if v is not None and math.isfinite(v))
    out = []
    best = None
    for hour, value in points:
        if best is None or value > best:
            best = value
        out.append((hour, best))
    return out


def hourly_step_curve(points: list[tuple[float, float]], max_hour: int = 12) -> list[float | None]:
    curve = best_curve(points)
    values = []
    best = None
    idx = 0
    for hour in range(max_hour + 1):
        while idx < len(curve) and curve[idx][0] <= hour:
            best = curve[idx][1]
            idx += 1
        values.append(best)
    return values


def default_efficient_roots() -> list[Path]:
    worktrees = sorted(
        ROOT.glob("ear-worktrees/*/docker_runs"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )
    return [*worktrees, ROOT / "mle-bench-agents/efficient-auto-research/docker_runs"]


def _locate_efficient_run(
    run_tag: str,
    comp: str,
    roots: list[Path],
) -> tuple[Path | None, Path | None]:
    for base in roots:
        workspace = base / f"{run_tag}_{comp}" / "workspace"
        reports = [
            workspace / "runs" / run_tag / "report.json",
            workspace / "report.json",
            *sorted((workspace / "runs").glob("*/report.json")),
        ]
        report = next((path for path in reports if path.is_file()), None)
        if report is not None:
            return report, report.parent / "traces"
    return None, None


def parse_efficient(
    run_tag: str,
    roots: list[Path] | None = None,
) -> dict[str, list[tuple[float, float]]]:
    roots = roots or default_efficient_roots()
    result: dict[str, list[tuple[float, float]]] = {}
    for comp in COMPS:
        report, trace_dir = _locate_efficient_run(run_tag, comp, roots)
        points: list[tuple[float, float]] = []
        if report is not None:
            data = json.loads(report.read_text())
            for row in data.get("step_log", []):
                metric = row.get("metric")
                elapsed = row.get("elapsed_seconds")
                if metric is not None and elapsed is not None:
                    points.append((float(elapsed) / 3600.0, float(metric)))
        if not points:
            traces = trace_dir.glob("step_*.json") if trace_dir is not None else []
            for trace in sorted(traces):
                data = json.loads(trace.read_text())
                metric = data.get("metric")
                elapsed = data.get("elapsed_seconds")
                if metric is not None and elapsed is not None:
                    points.append((float(elapsed) / 3600.0, float(metric)))
        result[comp] = points
    return result


def parse_mlevolve() -> dict[str, list[tuple[float, float]]]:
    result: dict[str, list[tuple[float, float]]] = {}
    for comp, run_dir in MLEVOLVE_RUNS.items():
        journal = run_dir / "logs/journal.json"
        points: list[tuple[float, float]] = []
        if journal.exists():
            nodes = json.loads(journal.read_text()).get("nodes", [])
            times = [n.get("ctime") for n in nodes if n.get("ctime") is not None]
            start = min(times) if times else None
            for node in nodes:
                metric = node.get("metric")
                value = metric.get("value") if isinstance(metric, dict) else metric
                ctime = node.get("ctime")
                if value is None or ctime is None or start is None:
                    continue
                points.append(((float(ctime) - float(start)) / 3600.0, float(value)))
        result[comp] = points
    return result


def aggregate_hourly(agent_points: dict[str, list[tuple[float, float]]], max_hour: int = 12) -> list[float | None]:
    per_comp = [hourly_step_curve(agent_points.get(comp, []), max_hour=max_hour) for comp in COMPS]
    out = []
    for idx in range(max_hour + 1):
        vals = [series[idx] for series in per_comp if series[idx] is not None]
        out.append(sum(vals) / len(vals) if vals else None)
    return out


def plot(all_points: dict[str, dict[str, list[tuple[float, float]]]], out_path: Path, max_hour: int = 12) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {
        "efficient-fixed": "#1f77b4",
        "MLEvolve": "#ff7f0e",
    }
    hours = list(range(max_hour + 1))
    fig, axes = plt.subplots(2, 2, figsize=(14, 9), dpi=160)
    axes = axes.ravel()

    ax = axes[0]
    for agent, points in all_points.items():
        y = aggregate_hourly(points, max_hour=max_hour)
        ax.plot(hours, y, marker="o", label=agent, color=colors.get(agent))
    ax.set_title("Average Best Score Across 3 Tasks")
    ax.set_xlabel("Hour")
    ax.set_ylabel("Average best score")
    ax.grid(True, alpha=0.25)
    ax.legend()

    for ax, comp in zip(axes[1:], COMPS):
        for agent, points in all_points.items():
            y = hourly_step_curve(points.get(comp, []), max_hour=max_hour)
            ax.plot(hours, y, marker="o", label=agent, color=colors.get(agent))
        ax.set_title(SHORT[comp])
        ax.set_xlabel("Hour")
        ax.set_ylabel("Best score")
        ax.grid(True, alpha=0.25)
    fig.suptitle("Hourly Best Score Comparison: fixed efficient vs MLEvolve", fontsize=14)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)


def write_summary(all_points: dict[str, dict[str, list[tuple[float, float]]]], out_path: Path, max_hour: int = 12) -> None:
    rows = []
    for agent, by_comp in all_points.items():
        avg = aggregate_hourly(by_comp, max_hour=max_hour)
        rows.append({
            "agent": agent,
            "final_average": avg[-1],
            "tasks": {
                SHORT[comp]: (hourly_step_curve(by_comp.get(comp, []), max_hour=max_hour)[-1])
                for comp in COMPS
            },
        })
    out_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-tag", required=True)
    parser.add_argument(
        "--ear-runs-root",
        action="append",
        default=None,
        help="EAR docker_runs directory; repeat to search multiple worktrees",
    )
    parser.add_argument("--out", default=None)
    parser.add_argument("--summary", default=None)
    parser.add_argument("--max-hour", type=int, default=12)
    args = parser.parse_args()

    out = Path(args.out) if args.out else ROOT / "docker-eval/plots" / f"{args.run_tag}_hourly_agent_scores.png"
    summary = Path(args.summary) if args.summary else ROOT / "docker-eval/plots" / f"{args.run_tag}_hourly_agent_scores.json"
    all_points = {
        "efficient-fixed": parse_efficient(
            args.run_tag,
            [Path(path) for path in args.ear_runs_root] if args.ear_runs_root else None,
        ),
        "MLEvolve": parse_mlevolve(),
    }
    plot(all_points, out, max_hour=args.max_hour)
    write_summary(all_points, summary, max_hour=args.max_hour)
    print(out)
    print(summary)


if __name__ == "__main__":
    main()
