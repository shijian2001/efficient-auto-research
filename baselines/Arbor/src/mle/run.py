"""Prepare an MLE-Bench workspace, run Arbor, and recover a final submission."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .adapter import AdapterError, WorkspaceSpec, prepare_workspace
from .common import sha256_file, write_json
from .state_store import find_bound_record, run_id_from_environment, state_root_from_environment


def _arbor_command() -> list[str]:
    executable = shutil.which("arbor")
    if executable:
        return [executable]
    return [sys.executable, "-c", "from arbor.cli.app import main; main()"]


def _trunk_branch_for_run(run_id: str) -> str:
    component = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(run_id)).strip(".-")
    return f"arbor/mle/{component or 'run'}/trunk"


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


def _workspace_candidate(spec: WorkspaceSpec) -> RecoveryCandidate | None:
    submission = spec.workspace / "submission.csv"
    solution = spec.workspace / "solution.py"
    if not submission.is_file() or submission.is_symlink():
        return None
    try:
        run_id = run_id_from_environment()
        record = find_bound_record(
            root=state_root_from_environment(),
            run_id=run_id,
            competition_id=spec.competition_id,
            solution=solution,
            submission=submission,
        )
    except Exception:
        return None
    try:
        metric = float(record["raw_metric_numeric"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(metric):
        return None
    return RecoveryCandidate(
        path=submission,
        source="verified_trunk",
        expected_sha256=str(record["submission_sha256"]),
        node_id=str(record["node_id"]),
        metric=metric,
    )


def _candidate_paths(spec: WorkspaceSpec) -> list[RecoveryCandidate]:
    workspace_candidate = _workspace_candidate(spec)
    return [workspace_candidate] if workspace_candidate is not None else []


def recover_submission(spec: WorkspaceSpec) -> Path:
    errors: list[str] = []
    for candidate in _candidate_paths(spec):
        final_dir = spec.run_dir / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        output = final_dir / "submission.csv"
        temporary = final_dir / ".submission.csv.tmp"
        shutil.copyfile(candidate.path, temporary)
        os.replace(temporary, output)
        published = spec.run_dir / "submission.csv"
        published_temporary = spec.run_dir / ".submission.csv.tmp"
        shutil.copyfile(output, published_temporary)
        os.replace(published_temporary, published)
        write_json(
            spec.run_dir / "submission_manifest.json",
            {
                "schema_version": 3,
                "run_id": run_id_from_environment(),
                "agent": "arbor",
                "agent_variant": os.environ.get(
                    "ARBOR_AGENT_VARIANT", "arbor-mle-adapter"
                ),
                "competition_id": spec.competition_id,
                "source": str(candidate.path),
                "source_kind": "arbor_declared_trunk",
                "source_node_id": candidate.node_id,
                "local_metric": candidate.metric,
                "submission": str(output),
                "published_submission": str(published),
                "sha256": sha256_file(output),
                "fallback": False,
                "format_validated": "attested_by_local_run",
                "evaluation_role": "local_only",
                "official_grading": "pending_external_mlebench",
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
    run_id = args.run_name or f"mle_{args.competition_id}"
    trunk_branch = _trunk_branch_for_run(run_id)
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
            trunk_branch=trunk_branch,
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

    state_root = spec.session_dir / "host-state"
    state_root.mkdir(parents=True, exist_ok=True)
    os.environ["ARBOR_MLE_STATE_ROOT"] = str(state_root)
    os.environ["ARBOR_MLE_RUN_ID"] = run_id

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
        run_id,
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
