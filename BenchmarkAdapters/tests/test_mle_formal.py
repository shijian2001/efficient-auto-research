from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from BenchmarkAdapters.contracts import AdapterError
from BenchmarkAdapters.formal_contract import ModelTrackConfig
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


def _model_config() -> ModelTrackConfig:
    """The shared model track every formal MLE cell binds its model to."""
    return ModelTrackConfig.load(
        ROOT / "BenchmarkAdapters/configs/model-track.gpt-5.6-terra-host-relay.json",
        formal=True,
    )


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


def test_formal_aggregate_requires_configured_n1_or_n3_unique_seeds() -> None:
    tasks = ("a",)
    one = calculate_seed_metrics(seed=0, task_ids=tasks, reports={})
    # N=1 and N=3 are both configured reporting modes; anything else is refused.
    assert aggregate_seeds([one])["reporting_label"] == "single_run"
    three = [one] + [
        calculate_seed_metrics(seed=seed, task_ids=tasks, reports={}) for seed in (1, 2)
    ]
    assert aggregate_seeds(three)["reporting_label"] == "avg_at_3"
    with pytest.raises(AdapterError, match="N=1 or N=3"):
        aggregate_seeds([one, calculate_seed_metrics(seed=1, task_ids=tasks, reports={})])
    # A seed set that does not fill the declared repetitions is refused.
    with pytest.raises(AdapterError, match="N=1 or N=3"):
        aggregate_seeds([one], outer_repetitions=3)
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
        "task_spec",
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
    # Every launcher now requires an explicit model, and Arbor's MLE cell is only
    # reachable through its registered patched variant.
    model_config = _model_config()
    request = MleLiteRequest(
        agent=agent,
        competition_id="spooky-author-identification",
        data_root=ROOT / "mle-bench-data",
        output_dir=tmp_path / agent,
        config_path=config_path,
        model=model_config.outer_model_id,
        agent_variant=(
            "arbor-benchmark-patched" if agent == "arbor" else "default"
        ),
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


def test_campaign_aggregate_refuses_incomplete_formal_evidence(tmp_path: Path) -> None:
    """A cell without complete formal evidence must stop the aggregate.

    Earlier behaviour folded a missing run into the denominator as a failure.
    That silently turned "we never ran this cell" into "this cell scored zero",
    which is a different claim. The aggregate is now fail-closed: every declared
    task must present both result.json and an immutable manifest.json, or no
    scorecard is produced at all.
    """
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
    # Only one of the 22 declared tasks has any evidence, and even that one is
    # missing its manifest, so the aggregate must refuse rather than score.
    with pytest.raises(AdapterError, match="missing formal evidence"):
        aggregate_campaign(protocol, tmp_path, "codex")


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
        model_config=_model_config(),
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
        model_config=_model_config(),
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

def test_every_result_path_records_token_usage() -> None:
    """No path that writes a result may leave token usage empty.

    Cost-per-point is only meaningful if every cell reports what it spent, and
    the cells that spend most are exactly the ones that end abnormally: a
    search-shaped Agent fills its whole wall-clock budget and is then killed, so
    it exits through the failure branch rather than the success one. When that
    branch omitted `tokens`, the priciest cells were recorded as free and the
    efficiency comparison inverted. This pins every writer, so a new exit path
    cannot quietly reintroduce the gap.
    """
    import ast

    for module, expected in (
        ("BenchmarkAdapters/MLEBenchLite/formal.py", 1),
        ("BenchmarkAdapters/MLEBenchLite/campaign.py", 1),
        ("BenchmarkAdapters/TerminalAO/supervisor.py", 3),
    ):
        source = (ROOT / module).read_text(encoding="utf-8")
        tree = ast.parse(source)
        constructed = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "BenchmarkRunResult"
        ]
        assert len(constructed) == expected, (
            f"{module} builds {len(constructed)} results, expected {expected}; "
            "a new one must also pass tokens="
        )
        for call in constructed:
            names = {kw.arg for kw in call.keywords}
            assert "tokens" in names, (
                f"{module}:{call.lineno} builds a BenchmarkRunResult without "
                "tokens=; a cell that ends here would report zero cost"
            )


def test_signature_survives_ids_that_look_like_exponents():
    """An id column is not a number just because Decimal will parse it.

    jigsaw ids look like "0061e98945132728". Decimal reads that as a finite
    number with an exponent no expansion can hold, so normalize() raises
    Overflow -- a DecimalException, but not InvalidOperation. Catching only the
    latter aborted every jigsaw cell in under two seconds, before the agent
    started, for all seven agents alike.
    """
    from BenchmarkAdapters.MLEBenchLite.adapter import _payload_signatures

    payload = b"id,toxic\n0061e98945132728,0.5\n00001cee341fdb12,0.5\n"
    signatures = _payload_signatures(payload)
    assert len(signatures) == 2, "expected both the raw and the canonical-csv digest"

    # The overflowing id must be preserved as text, so two files that differ
    # only in that column still hash differently.
    other = payload.replace(b"0061e98945132728", b"0061e98945132729")
    assert _payload_signatures(other).isdisjoint(signatures)


def _waiver(tmp_path, **overrides):
    payload = {
        "canonical_adapter_commit": "b" * 40,
        "adapter_commits": ["a" * 40, "b" * 40],
        "reason": "widened an exception clause; verified identical signatures",
    }
    payload.update(overrides)
    (tmp_path / "adapter-commit-waiver.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return tmp_path


def test_adapter_commits_need_a_waiver_to_mix(tmp_path):
    """Mixed adapter commits are refused unless a file on disk says why.

    Two revisions of the harness normally mean two incomparable harnesses, and
    silently picking one would split a scorecard row without saying so.
    """
    from BenchmarkAdapters.MLEBenchLite.campaign import _reconcile_adapter_commits

    one = {"a" * 40}
    assert _reconcile_adapter_commits(one, tmp_path, "ear") == "a" * 40

    mixed = {"a" * 40, "b" * 40}
    with pytest.raises(AdapterError, match="mix adapter commits"):
        _reconcile_adapter_commits(mixed, tmp_path, "ear")

    assert _reconcile_adapter_commits(mixed, _waiver(tmp_path), "ear") == "b" * 40


def test_waiver_cannot_be_vague_or_incomplete(tmp_path):
    """A waiver only excuses the commits it names, and only with a reason."""
    from BenchmarkAdapters.MLEBenchLite.campaign import _reconcile_adapter_commits

    mixed = {"a" * 40, "b" * 40}

    _waiver(tmp_path, adapter_commits=["a" * 40])
    with pytest.raises(AdapterError, match="does not cover"):
        _reconcile_adapter_commits(mixed, tmp_path, "ear")

    _waiver(tmp_path, reason="   ")
    with pytest.raises(AdapterError, match="states no reason"):
        _reconcile_adapter_commits(mixed, tmp_path, "ear")

    _waiver(tmp_path, canonical_adapter_commit="c" * 40)
    with pytest.raises(AdapterError, match="canonical commit that no cell used"):
        _reconcile_adapter_commits(mixed, tmp_path, "ear")

    # A third commit nobody declared still fails, waiver or not.
    _waiver(tmp_path)
    with pytest.raises(AdapterError, match="does not cover"):
        _reconcile_adapter_commits(mixed | {"c" * 40}, tmp_path, "ear")


def test_cli_harness_addendum_leaves_the_frozen_spec_alone():
    """The budget briefing is additive: the shared spec stays byte-identical.

    Codex and Claude Code need to be told things the other Agents get
    structurally, but the frozen task specification is what makes their cells
    comparable to everyone else's. If closing that gap moved the spec digest,
    every already-scored cell would silently belong to a different protocol.
    """
    from BenchmarkAdapters.MLEBenchLite.adapter import cli_harness_instruction
    from BenchmarkAdapters.task_specs import task_spec_digest, task_spec_text

    spec = task_spec_text("mle-bench-lite")
    instruction = cli_harness_instruction(43200)

    assert instruction.startswith(spec)
    assert task_spec_digest("mle-bench-lite") == (
        "2792c2280d603178072eb96e17f1a144d4654b7ac08f63fe9bf06714453c7780"
    )

    # The addendum states the working agreement and never leaks task content:
    # no competition name, no hint about what the data looks like.
    addendum = instruction[len(spec):]
    assert "DEADLINE.txt" in addendum
    assert "43200 seconds" in addendum
    assert "12 hours" in addendum
    for leak in ("spooky", "jigsaw", "mlsp", "author", "toxic"):
        assert leak not in addendum.lower()


def test_cli_harness_budget_is_rendered_from_the_real_cell_budget():
    """The stated budget tracks the cell, so a smoke run cannot claim 12 hours."""
    from BenchmarkAdapters.MLEBenchLite.adapter import cli_harness_instruction

    assert "1 hour (3600 seconds)" in cli_harness_instruction(3600)
    assert "2 hours (7200 seconds)" in cli_harness_instruction(7200)
    assert "30 minutes (1800 seconds)" in cli_harness_instruction(1800)
    with pytest.raises(AdapterError, match="positive budget"):
        cli_harness_instruction(0)


def test_only_the_cli_agents_are_briefed(tmp_path):
    """Agents that run their own loop keep the untouched specification.

    They already receive the budget through ``--budget-seconds`` and iterate
    against it internally; handing them a second, prose copy of the same
    contract would be a different task description for one half of the field.
    """
    from BenchmarkAdapters.MLEBenchLite.adapter import prepare_workspace
    from BenchmarkAdapters.task_specs import task_spec_text

    data_root = Path.home() / ".cache/mle-bench/data"
    if not (data_root / "spooky-author-identification/prepared/public").is_dir():
        pytest.skip("prepared MLE-Bench data is not available")

    request = MleLiteRequest(
        agent="codex",
        competition_id="spooky-author-identification",
        data_root=data_root,
        output_dir=tmp_path / "out",
        model="test-model",
        timeout_seconds=43200,
    )
    workspace = prepare_workspace(request)

    # AGENT_TASK.md is what the native Agents read, and it is the frozen text.
    agent_task = (workspace.workspace_dir / "AGENT_TASK.md").read_text(encoding="utf-8")
    assert agent_task.startswith(task_spec_text("mle-bench-lite"))
    assert "single-invocation" not in agent_task

    deadline_file = workspace.workspace_dir / "DEADLINE.txt"
    deadline = int(deadline_file.read_text(encoding="utf-8").splitlines()[0])
    assert 43100 < deadline - int(__import__("time").time()) <= 43200


def test_codex_is_told_that_a_bare_reply_ends_its_run(tmp_path):
    """Only Codex gets the session rule, because only Codex has that failure.

    `codex exec` treats a reply with no tool call as the end of the session, so
    a closing summary quits the run; Claude Code has no such rule and telling it
    otherwise would be a false statement about its own harness.
    """
    data_root = Path.home() / ".cache/mle-bench/data"
    if not (data_root / "spooky-author-identification/prepared/public").is_dir():
        pytest.skip("prepared MLE-Bench data is not available")

    prompts = {}
    for agent in ("codex", "claude-code"):
        request = MleLiteRequest(
            agent=agent,
            competition_id="spooky-author-identification",
            data_root=data_root,
            output_dir=tmp_path / agent,
            model="test-model",
            timeout_seconds=43200,
            dry_run=True,
        )
        prompts[agent] = MleLiteAdapter(agent).build_command(request).argv[-1]

    assert "How this session ends" in prompts["codex"]
    assert "How this session ends" not in prompts["claude-code"]
    # Both still carry the same budget agreement.
    for prompt in prompts.values():
        assert "12 hours (43200 seconds)" in prompt
