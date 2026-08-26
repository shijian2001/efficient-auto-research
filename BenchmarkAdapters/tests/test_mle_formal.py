from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from BenchmarkAdapters.contracts import AdapterError
from BenchmarkAdapters.MLEBenchLite.aggregate import aggregate_seeds, calculate_seed_metrics
from BenchmarkAdapters.MLEBenchLite.adapter import MleLiteAdapter, MleLiteRequest
from BenchmarkAdapters.MLEBenchLite.campaign import (
    MleCampaignCell,
    aggregate_campaign,
    build_mle_protocol,
    campaign_cells,
    run_campaign_cell,
)
from BenchmarkAdapters.MLEBenchLite.native_wrappers import run_ai_scientist, run_ml_master
from BenchmarkAdapters.MLEBenchLite.grading import grade_submission, metric_is_lower_better
from BenchmarkAdapters.MLEBenchLite.membership import (
    data_manifest_digest,
    load_data_manifest,
    load_lite_task_ids,
    require_lite_task,
    split_digest,
    validate_lite_data_root,
    validate_mlebench_source_identity,
    verify_task_archive,
)
from BenchmarkAdapters.registry import ROOT
from BenchmarkAdapters.registry import AGENTS


def test_frozen_lite_registry_contains_exactly_22_prepared_tasks() -> None:
    tasks = load_lite_task_ids()
    assert len(tasks) == 22
    assert len(set(tasks)) == 22
    assert validate_lite_data_root(ROOT / "mle-bench-data") == tasks
    assert len(split_digest()) == 64
    manifest = load_data_manifest()
    assert len(manifest["tasks"]) == 22
    assert data_manifest_digest() == manifest["manifest_digest"]
    validate_mlebench_source_identity()
    verify_task_archive(ROOT / "mle-bench-data", "spooky-author-identification")


def test_non_lite_task_is_rejected() -> None:
    with pytest.raises(AdapterError, match="not in the frozen"):
        require_lite_task("definitely-not-a-lite-task")


def test_host_owned_official_grader_saves_json(tmp_path: Path) -> None:
    competition = "spooky-author-identification"
    submission = (
        ROOT
        / "mle-bench-data"
        / competition
        / "prepared/public/sample_submission.csv"
    )
    grade = grade_submission(
        competition_id=competition,
        submission=submission,
        data_root=ROOT / "mle-bench-data",
        report_path=tmp_path / "competition_report.json",
    )
    assert grade.valid
    assert grade.report["competition_id"] == competition
    assert grade.report["valid_submission"] is True
    assert isinstance(grade.report["score"], float)
    assert json.loads(grade.report_path.read_text()) == grade.report
    assert len(grade.grader_worker_sha256) == 64


def test_official_grader_report_is_not_overwritten(tmp_path: Path) -> None:
    competition = "spooky-author-identification"
    submission = (
        ROOT
        / "mle-bench-data"
        / competition
        / "prepared/public/sample_submission.csv"
    )
    report = tmp_path / "competition_report.json"
    grade_submission(
        competition_id=competition,
        submission=submission,
        data_root=ROOT / "mle-bench-data",
        report_path=report,
    )
    with pytest.raises(AdapterError, match="refusing to overwrite"):
        grade_submission(
            competition_id=competition,
            submission=submission,
            data_root=ROOT / "mle-bench-data",
            report_path=report,
        )


def test_failures_remain_in_seed_denominator_and_raw_scores_are_not_averaged() -> None:
    tasks = tuple(f"task-{index:02d}" for index in range(22))
    reports = {
        task_id: {
            "valid_submission": True,
            "score": 1000.0 + index,
            "above_median": index < 11,
            "any_medal": index < 5,
            "gold_medal": index < 2,
        }
        for index, task_id in enumerate(tasks[:20])
    }
    seed = calculate_seed_metrics(seed=0, task_ids=tasks, reports=reports)
    assert seed.total_tasks == 22
    assert seed.failures == 2
    assert seed.valid_rate == 20 / 22
    assert seed.above_median_rate == 11 / 22
    assert seed.any_medal_rate == 5 / 22
    assert seed.gold_rate == 2 / 22

    aggregate = aggregate_seeds(
        [
            seed,
            calculate_seed_metrics(seed=1, task_ids=tasks, reports=reports),
            calculate_seed_metrics(seed=2, task_ids=tasks, reports={}),
        ]
    )
    assert aggregate["num_seeds"] == 3
    assert aggregate["tasks_per_seed"] == 22
    assert aggregate["total_failures"] == 26
    assert aggregate["raw_scores_averaged_across_tasks"] is False
    assert "valid_rate" in aggregate["metrics"]


def test_formal_aggregate_requires_three_unique_seeds() -> None:
    tasks = ("a",)
    one = calculate_seed_metrics(seed=0, task_ids=tasks, reports={})
    with pytest.raises(AdapterError, match="at least three"):
        aggregate_seeds([one])
    with pytest.raises(AdapterError, match="duplicate"):
        aggregate_seeds([one, one, one])


def test_formal_protocol_builds_complete_seven_agent_grid(tmp_path: Path) -> None:
    protocol = build_mle_protocol(seeds=(11, 12, 13))
    assert protocol.asset_digests["data_manifest"] == data_manifest_digest()
    assert set(protocol.asset_digests) == {
        "data_manifest",
        "grader_worker",
        "mlebench_lock",
        "official_low_split",
    }
    cells = campaign_cells(protocol, tmp_path)
    assert len(cells) == 7 * 3 * 22
    assert len({cell.run_id for cell in cells}) == len(cells)
    assert {cell.agent for cell in cells} == {
        "ear",
        "mlevolve",
        "arbor",
        "codex",
        "claude-code",
        "ml-master-2",
        "ai-scientist",
    }


@pytest.mark.parametrize("agent", tuple(AGENTS))
def test_every_mle_launcher_declares_one_explicit_final_artifact(
    agent: str,
    tmp_path: Path,
) -> None:
    config_path = (
        ROOT / "baselines/EvoMaster/configs/ml_master_2/deepseek-v3.2-example.yaml"
        if agent == "ml-master-2"
        else None
    )
    request = MleLiteRequest(
        agent=agent,
        competition_id="spooky-author-identification",
        data_root=ROOT / "mle-bench-data",
        output_dir=tmp_path / agent,
        config_path=config_path,
        dry_run=True,
    )
    command = MleLiteAdapter(agent).build_command(request)
    assert command.artifact_path is not None
    assert command.artifact_path.name == "submission.csv"
    assert str(command.artifact_path).startswith(str((tmp_path / agent).resolve()))
    if agent == "ai-scientist":
        command_text = " ".join(command.argv)
        assert "--data-dir" in command.argv
        assert "prepared/public" in command_text
        assert "--mlebench-data-dir" not in command.argv
        assert "--skip-final-validation" in command.argv


def test_native_wrappers_publish_only_declared_final_path(tmp_path: Path) -> None:
    ai_output = tmp_path / "ai"
    child = (
        "from pathlib import Path; "
        f"p=Path({str(ai_output / 'jobs/job-new/workspace/submission')!r}); "
        "p.mkdir(parents=True); (p/'submission.csv').write_text('id,y\\n1,1\\n')"
    )
    run_ai_scientist(ai_output, [sys.executable, "-c", child])
    assert (ai_output / "submission.csv").read_text() == "id,y\n1,1\n"

    master_output = tmp_path / "master"
    workspace = master_output / "workspace"
    child = (
        "from pathlib import Path; "
        f"p=Path({str(workspace / 'best_submission')!r}); "
        "p.mkdir(parents=True); (p/'submission.csv').write_text('id,y\\n1,2\\n')"
    )
    run_ml_master(master_output, workspace, [sys.executable, "-c", child])
    assert (master_output / "submission.csv").read_text() == "id,y\n1,2\n"


def test_campaign_aggregate_keeps_missing_runs_in_denominator(tmp_path: Path) -> None:
    protocol = build_mle_protocol(seeds=(0, 1, 2))
    task_id = protocol.task_ids[0]
    for seed in protocol.seeds:
        run_dir = tmp_path / "codex" / f"seed-{seed}" / task_id
        (run_dir / "grading").mkdir(parents=True)
        report = {
            "competition_id": task_id,
            "valid_submission": True,
            "score": 0.5,
            "above_median": True,
            "any_medal": False,
            "gold_medal": False,
        }
        (run_dir / "grading/competition_report.json").write_text(json.dumps(report))
        (run_dir / "result.json").write_text(
            json.dumps(
                {
                    "protocol_digest": protocol.digest,
                    "agent": "codex",
                    "task_id": task_id,
                    "seed": seed,
                    "status": "completed",
                    "tokens": {},
                    "cost": {},
                }
            )
        )
    aggregate = aggregate_campaign(protocol, tmp_path, "codex")
    assert aggregate["metrics"]["valid_rate"]["mean"] == pytest.approx(1 / 22)
    assert aggregate["total_failures"] == 63
    assert aggregate["raw_scores_averaged_across_tasks"] is False


def test_failed_official_grade_preserves_published_artifact_hash(
    tmp_path: Path,
    monkeypatch,
) -> None:
    protocol = build_mle_protocol()
    task_id = protocol.task_ids[0]
    cell = MleCampaignCell("codex", task_id, 0, tmp_path / "cell")
    monkeypatch.setattr(
        "BenchmarkAdapters.MLEBenchLite.campaign.validate_mle_protocol",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "BenchmarkAdapters.MLEBenchLite.campaign.verify_task_archive",
        lambda *_args, **_kwargs: None,
    )

    def fail_after_publish(*, run_dir: Path, manifest, **_kwargs):
        manifest.write(run_dir / "manifest.json")
        artifact = run_dir / "artifacts/final/submission.csv"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("id,target\n1,invalid\n", encoding="utf-8")
        report = run_dir / "grading/competition_report.json"
        report.parent.mkdir(parents=True)
        report.write_text(
            json.dumps({"valid_submission": False, "score": None}),
            encoding="utf-8",
        )
        raise AdapterError("official MLE grader rejected submission")

    monkeypatch.setattr("BenchmarkAdapters.MLEBenchLite.campaign.run_formal_mle", fail_after_publish)
    result = run_campaign_cell(
        cell=cell,
        protocol=protocol,
        data_root=tmp_path,
        formal=False,
    )
    assert result.status.value == "invalid_artifact"
    assert result.artifact_path is not None
    assert len(result.artifact_sha256 or "") == 64
    assert result.metrics["valid_submission"] is False


def test_formal_cell_uses_wall_clock_not_one_step_default(tmp_path: Path, monkeypatch) -> None:
    protocol = build_mle_protocol()
    task_id = protocol.task_ids[0]
    cell = MleCampaignCell("codex", task_id, 0, tmp_path / "cell")
    monkeypatch.setattr(
        "BenchmarkAdapters.MLEBenchLite.campaign.validate_mle_protocol",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "BenchmarkAdapters.MLEBenchLite.campaign.verify_task_archive",
        lambda *_args, **_kwargs: None,
    )
    captured = {}

    def capture_request(*, request, **_kwargs):
        captured["request"] = request
        raise AdapterError("synthetic stop")

    monkeypatch.setattr("BenchmarkAdapters.MLEBenchLite.campaign.run_formal_mle", capture_request)
    run_campaign_cell(
        cell=cell,
        protocol=protocol,
        data_root=tmp_path,
        formal=False,
    )
    request = captured["request"]
    assert request.timeout_seconds == protocol.wall_clock_seconds
    assert request.steps == 1000
    assert request.max_turns == 1000


LOWER_IS_BETTER_LITE_TASKS = frozenset(
    {
        "denoising-dirty-documents",
        "dog-breed-identification",
        "dogs-vs-cats-redux-kernels-edition",
        "leaf-classification",
        "new-york-city-taxi-fare-prediction",
        "nomad2018-predict-transparent-conductors",
        "spooky-author-identification",
    }
)


def test_official_metric_direction_matches_the_frozen_lite_leaderboards() -> None:
    tasks = load_lite_task_ids()
    assert LOWER_IS_BETTER_LITE_TASKS <= set(tasks)
    directions = {
        task: metric_is_lower_better(
            competition_id=task, data_root=ROOT / "mle-bench-data"
        )
        for task in tasks
    }
    assert {task for task, lower in directions.items() if lower} == set(
        LOWER_IS_BETTER_LITE_TASKS
    )
    assert sum(directions.values()) == 7
    assert len(directions) - sum(directions.values()) == 15


def test_metric_direction_rejects_non_lite_tasks() -> None:
    with pytest.raises(AdapterError, match="not in the frozen"):
        metric_is_lower_better(
            competition_id="definitely-not-a-lite-task",
            data_root=ROOT / "mle-bench-data",
        )


def test_ml_master_generated_config_carries_the_official_metric_direction(
    tmp_path: Path,
) -> None:
    import subprocess

    import yaml

    source_root = AGENTS["ml-master-2"].install_path
    template = source_root / "configs/ml_master_2/deepseek-v3.2-example.yaml"
    assert yaml.safe_load(template.read_text(encoding="utf-8"))["is_lower_better"] is False
    worker = (
        ROOT / "BenchmarkAdapters/MLEBenchLite/ml_master_config_worker.py"
    )
    public_dir = tmp_path / "source-public"
    public_dir.mkdir()
    (public_dir / "train.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    for competition, flag, expected in (
        ("spooky-author-identification", "true", True),
        ("aerial-cactus-identification", "false", False),
    ):
        destination = tmp_path / competition / "config.yaml"
        completed = subprocess.run(
            [
                str(source_root / ".venv/bin/python"),
                str(worker),
                "--template",
                str(template),
                "--destination",
                str(destination),
                "--competition-id",
                competition,
                "--public-dir",
                str(public_dir),
                "--staged-data-root",
                str(tmp_path / competition / "public-data"),
                "--workspace-dir",
                str(tmp_path / competition / "workspace"),
                "--gpu-id",
                "0",
                "--is-lower-better",
                flag,
                "--model",
                "test-model",
                "--model-parameters-json",
                json.dumps({"temperature": 1.0}),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        payload = yaml.safe_load(destination.read_text(encoding="utf-8"))
        assert payload["competition_id"] == competition
        assert payload["is_lower_better"] is expected


def test_ml_master_config_generation_requires_an_explicit_metric_direction(
    tmp_path: Path,
) -> None:
    import subprocess

    source_root = AGENTS["ml-master-2"].install_path
    completed = subprocess.run(
        [
            str(source_root / ".venv/bin/python"),
            str(ROOT / "BenchmarkAdapters/MLEBenchLite/ml_master_config_worker.py"),
            "--template",
            str(source_root / "configs/ml_master_2/deepseek-v3.2-example.yaml"),
            "--destination",
            str(tmp_path / "config.yaml"),
            "--competition-id",
            "spooky-author-identification",
            "--public-dir",
            str(tmp_path),
            "--staged-data-root",
            str(tmp_path / "public-data"),
            "--workspace-dir",
            str(tmp_path / "workspace"),
            "--gpu-id",
            "0",
            "--model",
            "test-model",
            "--model-parameters-json",
            json.dumps({"temperature": 1.0}),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "--is-lower-better" in completed.stderr
