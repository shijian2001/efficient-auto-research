"""Create the isolated Git workspace consumed by Arbor."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .common import (
    AdapterError,
    discover_public_sample,
    infer_metric_direction,
    sha256_file,
    write_json,
)


@dataclass(frozen=True)
class WorkspaceSpec:
    competition_id: str
    public_dir: Path
    description_path: Path
    run_dir: Path
    workspace: Path
    session_dir: Path
    validation_url: str
    metric_direction: str
    sample_submission: Path
    research_config: Path
    trunk_branch: str | None = None


def _built_in_plugin_path() -> Path:
    return Path(__file__).resolve().parents[1] / "plugins" / "mle_kaggle.yaml"


def _write_project_plugin(spec: WorkspaceSpec) -> None:
    plugin = yaml.safe_load(_built_in_plugin_path().read_text(encoding="utf-8"))
    plugin["name"] = "mle_adapter"
    plugin["description"] = (
        "Arbor-side MLE-Bench adapter. Uses public data and local validation only; "
        "official private-label grading remains outside Arbor."
    )
    plugin["eval_contract"] = {
        "metric_direction": spec.metric_direction,
        "eval_cmd": "bash {cwd}/eval.sh run",
        "eval_cmd_test": "bash {cwd}/eval.sh verify --node-id {node_id}",
        "evaluation_receipt_path": "results/mle_eval_receipt.json",
        "host_state_root_env": "ARBOR_MLE_STATE_ROOT",
        "host_run_id_env": "ARBOR_MLE_RUN_ID",
        "solution_path": "solution.py",
        "evaluation_semantics": "candidate_local_metric_plus_format_validation",
        "test_semantics": "artifact_verification_only",
        "official_grading": "external_mlebench",
        "dataset_info": (
            f"MLE-Bench competition {spec.competition_id}; public files are under input/. "
            "Private labels are unavailable."
        ),
        "submission_path": "submission.csv",
        "sample_submission_path": ".mle/public_sample_submission.csv",
        "contamination": {
            "is_public": True,
            "notes": "Public Kaggle competition data; private MLE-Bench answers are isolated.",
        },
    }
    plugin["protected_paths"] = [
        "input",
        "description.md",
        ".mle/adapter.json",
        ".mle/public_sample_submission.csv",
        "eval.sh",
        "research_config.yaml",
        "plugins/mle_adapter.yaml",
    ]
    plugin["required_outputs"] = ["submission.csv"]
    plugin["lifecycle_hooks"] = {
        "after_executor": {
            "prompt": "Snapshot submission.csv only when its verified evaluation state matches the current solution and artifact hashes.",
            "require_verified_state": True,
        }
    }
    contract = f"""

## MLE-Bench Adapter Contract

- Competition ID: `{spec.competition_id}`.
- Read only public competition files from `input/` and the task statement from `description.md`.
- Never search for or access any `prepared/private` or `/private/data` path.
- Implement the complete reproducible pipeline in `solution.py`.
- Every evaluation must create `submission.csv` and print exactly one final `METRIC=<finite-float>` line.
- `METRIC` is a local validation metric computed from public training data. The optimization direction is `{spec.metric_direction}`.
- Run `bash eval.sh run` after changes. Format validity is checked remotely without exposing private scores or labels.
    - `bash eval.sh verify` only verifies the host-owned record bound to the current code and submission hashes. It is not an independent holdout or the official MLE-Bench score.
- Do not edit `eval.sh`, `.mle/`, `input`, `description.md`, the project config, or the adapter plugin.
- The seed submission is only an emergency format-valid fallback; its placeholder metric is not experimental evidence.
"""
    plugin["meta_init_inject"] = str(plugin.get("meta_init_inject", "")) + contract
    plugin["sub_workflow_inject"] = str(plugin.get("sub_workflow_inject", "")) + contract
    path = spec.workspace / "plugins" / "mle_adapter.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(plugin, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _write_solution(spec: WorkspaceSpec) -> None:
    placeholder = "-1e30" if spec.metric_direction == "maximize" else "1e30"
    (spec.workspace / "solution.py").write_text(
        """from pathlib import Path
import shutil


def main() -> None:
    sample = Path('.mle/public_sample_submission.csv')
    output = Path('submission.csv')
    shutil.copyfile(sample, output)
    print('Seed fallback submission copied. Replace this with a real public-data validation pipeline.')
    print('METRIC=%s')


if __name__ == '__main__':
    main()
""" % placeholder,
        encoding="utf-8",
    )


def _write_workspace_files(
    spec: WorkspaceSpec,
    *,
    provider: str | None,
    model: str | None,
    base_url: str | None,
    time_budget: int | None,
) -> None:
    os.symlink(spec.public_dir, spec.workspace / "input", target_is_directory=True)
    shutil.copy2(spec.description_path, spec.workspace / "description.md")
    _write_solution(spec)

    (spec.workspace / "eval.sh").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "mode=${1:-run}\n"
        "if [ $# -gt 0 ]; then shift; fi\n"
        "exec python -m arbor.mle.eval_runner \"$mode\" "
        "--workspace \"$(cd \"$(dirname \"$0\")\" && pwd)\" \"$@\"\n",
        encoding="utf-8",
    )
    (spec.workspace / "eval.sh").chmod(0o755)

    config: dict[str, Any] = {
        "plugin": "mle_adapter",
        "plugin_profile": "mle_bench_lite",
        "ui": {"interaction_mode": "auto"},
    }
    llm = {
        key: value
        for key, value in {"provider": provider, "model": model, "base_url": base_url}.items()
        if value
    }
    if llm:
        config["llm"] = llm
    if time_budget is not None:
        config["time_budget"] = time_budget
    if spec.trunk_branch:
        config["trunk_branch"] = spec.trunk_branch
    spec.research_config.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    _write_project_plugin(spec)

    (spec.workspace / "MLE_ADAPTER.md").write_text(
        f"""# Arbor MLE-Bench Workspace

- Competition: `{spec.competition_id}`
- Public data: `input/`
- Metric direction: `{spec.metric_direction}`
- Candidate entrypoint: `solution.py`
- Evaluation: `bash eval.sh run`
- Required output: `submission.csv`

The validation service checks submission format only. `eval.sh verify` is artifact verification, not an independent B_test. Official MLE-Bench grading is intentionally performed outside this workspace after final artifact recovery.
""",
        encoding="utf-8",
    )
    (spec.workspace / ".gitignore").write_text(
        ".arbor/\nresults/\n__pycache__/\n*.pyc\n",
        encoding="utf-8",
    )

    immutable = {
        "description.md": sha256_file(spec.workspace / "description.md"),
        ".mle/public_sample_submission.csv": sha256_file(spec.sample_submission),
    }
    write_json(
        spec.workspace / ".mle" / "adapter.json",
        {
            "schema_version": 1,
            "competition_id": spec.competition_id,
            "public_dir": str(spec.public_dir),
            "validation_url": spec.validation_url,
            "metric_direction": spec.metric_direction,
            "submission_path": "submission.csv",
            "solution_path": "solution.py",
            "evaluation_receipt_path": "results/mle_eval_receipt.json",
            "immutable": immutable,
        },
    )


def _init_git(workspace: Path, *, trunk_branch: str | None = None) -> None:
    try:
        subprocess.run(
            ["git", "init"],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "checkout", "-b", "main"],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Arbor MLE Adapter",
                "-c",
                "user.email=arbor-mle@localhost",
                "commit",
                "-m",
                "Initialize Arbor MLE-Bench workspace",
            ],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
        if trunk_branch:
            subprocess.run(
                ["git", "branch", trunk_branch],
                cwd=workspace,
                check=True,
                capture_output=True,
                text=True,
            )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AdapterError(f"could not initialize adapter Git workspace: {exc}") from exc


def prepare_workspace(
    *,
    competition_id: str,
    public_dir: Path,
    description_path: Path,
    run_dir: Path,
    validation_url: str,
    metric_direction: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    time_budget: int | None = None,
    trunk_branch: str | None = None,
    force: bool = False,
) -> WorkspaceSpec:
    public_dir = public_dir.resolve()
    description_path = description_path.resolve()
    run_dir = run_dir.resolve()
    workspace = run_dir / "workspace"
    session_dir = run_dir / "arbor-session"
    if not public_dir.is_dir():
        raise AdapterError(f"public data directory does not exist: {public_dir}")
    if not description_path.is_file():
        raise AdapterError(f"description file does not exist: {description_path}")
    if workspace.exists():
        if not force:
            raise AdapterError(f"workspace already exists: {workspace}; pass --force to replace it")
        shutil.rmtree(workspace)
    if force and session_dir.exists():
        shutil.rmtree(session_dir)
    workspace.mkdir(parents=True)
    session_dir.mkdir(parents=True, exist_ok=True)
    sample = workspace / ".mle" / "public_sample_submission.csv"
    discover_public_sample(public_dir, sample)
    direction = infer_metric_direction(competition_id, public_dir, metric_direction)
    spec = WorkspaceSpec(
        competition_id=competition_id,
        public_dir=public_dir,
        description_path=description_path,
        run_dir=run_dir,
        workspace=workspace,
        session_dir=session_dir,
        validation_url=validation_url,
        metric_direction=direction,
        sample_submission=sample,
        research_config=workspace / "research_config.yaml",
        trunk_branch=trunk_branch,
    )
    _write_workspace_files(
        spec,
        provider=provider,
        model=model,
        base_url=base_url,
        time_budget=time_budget,
    )
    fallback = run_dir / "submission.csv"
    shutil.copy2(sample, fallback)
    write_json(
        run_dir / "submission_manifest.json",
        {
            "schema_version": 1,
            "competition_id": competition_id,
            "source": str(sample),
            "submission": str(fallback),
            "sha256": sha256_file(fallback),
            "fallback": True,
            "official_grading": "external_mlebench",
        },
    )
    _init_git(workspace, trunk_branch=trunk_branch)
    return spec
