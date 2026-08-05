"""Prepare an MLE-Bench workspace, run Arbor, and recover a final submission."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .adapter import AdapterError, WorkspaceSpec, prepare_workspace
from .common import sha256_file, validate_submission_remote, write_json


def _arbor_command() -> list[str]:
    executable = shutil.which("arbor")
    if executable:
        return [executable]
    return [sys.executable, "-c", "from arbor.cli.app import main; main()"]


@dataclass(frozen=True)
class RecoveryCandidate:
    path: Path
    source: str
    expected_sha256: str
    node_id: str | None = None
    metric: float | None = None


def _read_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _snapshot_candidates(spec: WorkspaceSpec) -> list[RecoveryCandidate]:
    # Normal Arbor sessions store controller state below `.coordinator/`.
    # Keep the direct path as a compatibility fallback for early adapter
    # prototypes, but never merge their contents: the first valid tree wins.
    tree = None
    for tree_path in (
        spec.session_dir / ".coordinator" / "idea_tree.json",
        spec.session_dir / "idea_tree.json",
    ):
        tree = _read_json(tree_path)
        if tree is not None:
            break
    if tree is None:
        return []
    direction = str(tree.get("meta", {}).get("metric_direction") or spec.metric_direction)
    scored: list[tuple[float, str]] = []
    for node_id, node in tree.get("nodes", {}).items():
        if node.get("status") not in {"done", "merged"} or node.get("score") is None:
            continue
        try:
            score = float(node["score"])
        except (TypeError, ValueError):
            continue
        if math.isfinite(score):
            scored.append((score, str(node_id)))
    scored.sort(key=lambda item: item[0], reverse=direction != "minimize")

    candidates: list[RecoveryCandidate] = []
    snapshot_dir = spec.session_dir / "submissions"
    for score, node_id in scored:
        submission = snapshot_dir / f"{node_id}.csv"
        manifest = _read_json(snapshot_dir / f"{node_id}.json")
        if not submission.is_file() or submission.is_symlink() or manifest is None:
            continue
        expected = manifest.get("submission_sha256")
        try:
            manifest_metric = float(manifest["metric"])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            manifest.get("node_id") != node_id
            or manifest.get("competition_id") != spec.competition_id
            or manifest.get("metric_direction") != direction
            or manifest.get("official_grading") is not False
            or not isinstance(expected, str)
            or not math.isfinite(manifest_metric)
            or not math.isclose(manifest_metric, score, rel_tol=1e-9, abs_tol=1e-12)
            or sha256_file(submission) != expected
        ):
            continue
        candidates.append(
            RecoveryCandidate(
                path=submission,
                source="verified_snapshot",
                expected_sha256=expected,
                node_id=node_id,
                metric=score,
            )
        )
    return candidates


def _workspace_candidate(spec: WorkspaceSpec) -> RecoveryCandidate | None:
    state = _read_json(spec.workspace / "results" / "mle_eval_state.json")
    submission = spec.workspace / "submission.csv"
    solution = spec.workspace / "solution.py"
    if state is None or not submission.is_file() or submission.is_symlink():
        return None
    if not solution.is_file() or solution.is_symlink():
        return None
    expected_submission = state.get("submission_sha256")
    expected_solution = state.get("solution_sha256")
    if (
        state.get("schema_version") != 2
        or state.get("competition_id") != spec.competition_id
        or state.get("evaluation_status") != "verified"
        or state.get("format_validated") is not True
        or not isinstance(expected_submission, str)
        or not isinstance(expected_solution, str)
        or sha256_file(submission) != expected_submission
        or sha256_file(solution) != expected_solution
    ):
        return None
    try:
        metric = float(state["metric"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(metric):
        return None
    return RecoveryCandidate(
        path=submission,
        source="verified_trunk",
        expected_sha256=expected_submission,
        metric=metric,
    )


def _candidate_paths(spec: WorkspaceSpec) -> list[RecoveryCandidate]:
    candidates = _snapshot_candidates(spec)
    workspace_candidate = _workspace_candidate(spec)
    if workspace_candidate is not None and all(
        candidate.expected_sha256 != workspace_candidate.expected_sha256 for candidate in candidates
    ):
        candidates.append(workspace_candidate)
    return candidates


def recover_submission(spec: WorkspaceSpec) -> Path:
    errors: list[str] = []
    for candidate in _candidate_paths(spec):
        try:
            valid, message = validate_submission_remote(
                spec.validation_url, spec.competition_id, candidate.path
            )
        except AdapterError as exc:
            errors.append(f"{candidate.path}: {exc}")
            continue
        if not valid:
            errors.append(f"{candidate.path}: {message}")
            continue
        output = spec.run_dir / "submission.csv"
        temporary = spec.run_dir / ".submission.csv.tmp"
        shutil.copyfile(candidate.path, temporary)
        os.replace(temporary, output)
        write_json(
            spec.run_dir / "submission_manifest.json",
            {
                "schema_version": 2,
                "competition_id": spec.competition_id,
                "source": str(candidate.path),
                "source_kind": candidate.source,
                "source_node_id": candidate.node_id,
                "local_metric": candidate.metric,
                "submission": str(output),
                "sha256": sha256_file(output),
                "fallback": False,
                "format_validated": True,
                "official_grading": "external_mlebench",
            },
        )
        return output
    detail = "; ".join(errors) if errors else "no hash-verified candidate artifact exists"
    raise AdapterError(
        "no verified final submission found; the emergency public fallback remains in run-dir: "
        + detail
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--competition-id", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--desc-file", type=Path)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--validation-url", required=True)
    parser.add_argument(
        "--metric-direction",
        choices=("auto", "maximize", "minimize"),
        default="auto",
    )
    parser.add_argument("--provider", default=os.environ.get("ARBOR_LLM_PROVIDER"))
    parser.add_argument("--model", default=os.environ.get("MODEL") or os.environ.get("ARBOR_MODEL"))
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"))
    parser.add_argument("--time-budget", type=int)
    parser.add_argument("--instruction")
    parser.add_argument("--run-name")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extra: list[str] = []
    if "--" in argv:
        split = argv.index("--")
        extra = argv[split + 1 :]
        argv = argv[:split]
    args = _build_parser().parse_args(argv)
    description = args.desc_file or args.data_dir / "description.md"
    try:
        spec = prepare_workspace(
            competition_id=args.competition_id,
            public_dir=args.data_dir,
            description_path=description,
            run_dir=args.run_dir,
            validation_url=args.validation_url,
            metric_direction=args.metric_direction,
            provider=args.provider,
            model=args.model,
            base_url=args.base_url,
            time_budget=args.time_budget,
            force=args.force,
        )
    except AdapterError as exc:
        print(f"Arbor MLE adapter setup failed: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "workspace": str(spec.workspace),
                "session_dir": str(spec.session_dir),
                "metric_direction": spec.metric_direction,
            },
            sort_keys=True,
        )
    )
    if args.prepare_only:
        return 0

    instruction = args.instruction or (
        f"Develop the strongest reproducible solution for MLE-Bench competition {args.competition_id}. "
        "Use only public data, optimize the configured local validation metric, and leave a format-valid submission.csv."
    )
    command = _arbor_command() + [
        "run",
        instruction,
        "--yes",
        "--yes-cwd",
        str(spec.workspace),
        "--config",
        str(spec.research_config),
        "--workspace-dir",
        str(spec.session_dir),
        "--run-name",
        args.run_name or f"mle_{args.competition_id}",
        "--interaction-mode",
        "auto",
        "--no-dashboard-input",
        "--no-followup",
        "--no-webui",
    ] + extra
    completed = subprocess.run(command, cwd=spec.workspace)
    try:
        output = recover_submission(spec)
        print(f"Final format-valid submission: {output}")
    except AdapterError as exc:
        print(f"Final submission recovery failed: {exc}", file=sys.stderr)
        return completed.returncode or 3
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
