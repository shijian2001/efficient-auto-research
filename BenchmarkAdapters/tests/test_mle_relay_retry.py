from __future__ import annotations

import json
import socket
import tempfile
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from BenchmarkAdapters.contracts import AdapterError, CommandResult, CommandSpec
from BenchmarkAdapters.formal_contract import ModelTrackConfig
from BenchmarkAdapters.MLEBenchLite import adapter, campaign, formal
from BenchmarkAdapters.MLEBenchLite.retry import cell_execution_lock
from BenchmarkAdapters.protocol import sha256_file
from BenchmarkAdapters.records import BenchmarkRunResult, RunStatus
from BenchmarkAdapters.registry import ROOT


@pytest.fixture
def failed_cell(tmp_path, monkeypatch):
    protocol = campaign.build_mle_protocol()
    model = ModelTrackConfig.load(
        ROOT / "BenchmarkAdapters/configs/model-track.gpt-5.6-terra-host-relay.json", formal=True,
    )
    cell = campaign.MleCampaignCell("ai-scientist", protocol.task_ids[0], 0, tmp_path / "cell")
    variant = "ai-scientist@test-startup"
    manifest = campaign.build_manifest(
        cell=cell, protocol=protocol, gpu_id=0, formal=False,
        model_config=model, agent_variant=variant,
    )
    manifest.write(cell.run_dir / "manifest.json")
    output = cell.run_dir / "agent-output"
    (output / "tmp").mkdir(parents=True)
    (output / "relay.log").write_text("Traceback:\nOSError: AF_UNIX path too long\n")
    result = BenchmarkRunResult(
        run_id=cell.run_id, protocol_id=protocol.protocol_id, protocol_digest=protocol.digest,
        manifest_digest=manifest.digest, mode=protocol.mode, agent=cell.agent,
        task_id=cell.task_id, seed=0, status=RunStatus.FAILED, score_valid=False, score=None,
        metrics={}, artifact_path=None, artifact_sha256=None, wall_clock_seconds=0.1,
        failure_reason=f"RuntimeError: relay exited early; see {output / 'relay.log'}",
    )
    result.write(cell.run_dir / "result.json")
    for name in ("validate_mle_protocol", "verify_task_archive"):
        monkeypatch.setattr(campaign, name, lambda *args, **kwargs: None)
    monkeypatch.setattr("BenchmarkAdapters.run_logs.INDEX_ROOT", tmp_path / "index")
    return dict(cell=cell, protocol=protocol, data_root=tmp_path, formal=False,
                model_config=model, agent_variant=variant)


def _saved_files(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def test_retry_archives_failure_and_produces_a_new_scored_result(failed_cell, monkeypatch):
    cell = failed_cell["cell"]
    before = _saved_files(cell.run_dir)

    def agent_run(self, request, **kwargs):
        assert not (cell.run_dir / "result.json").exists()
        assert (cell.run_dir / "manifest.json").exists()
        submission = request.output_dir / "submission.csv"
        submission.parent.mkdir(parents=True)
        submission.write_text("id,target\n1,0.5\n")
        return submission

    def grade(*, report_path, **kwargs):
        report = {"valid_submission": True, "score": 0.75}
        report_path.parent.mkdir(parents=True)
        report_path.write_text(json.dumps(report))
        return SimpleNamespace(report=report, report_path=report_path)

    monkeypatch.setattr(adapter.MleLiteAdapter, "run", agent_run)
    monkeypatch.setattr(formal, "grade_submission", grade)
    outcome = campaign.run_campaign_cell(**failed_cell, retry_relay_startup=True)
    assert outcome.result.status is RunStatus.COMPLETED
    assert outcome.result.run_id == cell.run_id
    assert outcome.result.score == 0.75
    archives = list(cell.run_dir.parent.glob(".relay-startup-failures/cell/attempt-*"))
    assert len(archives) == 1
    assert _saved_files(archives[0] / "cell") == before
    audit = json.loads((archives[0] / "retry.json").read_text())
    assert audit["original_result_sha256"] == sha256_file(archives[0] / "cell/result.json")
    assert (cell.run_dir / "artifacts/final/submission.csv").is_file()


def test_existing_cell_requires_explicit_retry_and_is_unchanged(failed_cell):
    cell = failed_cell["cell"]
    before = _saved_files(cell.run_dir)
    with pytest.raises(AdapterError, match="already contains evidence"):
        campaign.run_campaign_cell(**failed_cell)
    assert _saved_files(cell.run_dir) == before


@pytest.mark.parametrize("change", ["scored", "work", "usage", "config", "digest", "other_failure"])
def test_retry_rejects_success_work_or_mismatched_evidence(failed_cell, change):
    cell = failed_cell["cell"]
    rp = cell.run_dir / "result.json"
    result = json.loads(rp.read_text())
    if change == "scored":
        result.update(status="completed", score_valid=True, score=0.9)
    elif change == "work":
        (cell.run_dir / "agent-output/submission.csv").write_text("id,target\n1,0.5\n")
    elif change == "usage":
        result["tokens"] = {"requests": 1}
    elif change == "config":
        failed_cell["agent_variant"] = "ai-scientist@other-version"
    elif change == "digest":
        mp = cell.run_dir / "manifest.json"
        manifest = json.loads(mp.read_text())
        manifest["model"] = "different-model"
        mp.write_text(json.dumps(manifest))
    else:
        result["failure_reason"] = "AdapterError: training failed"
    rp.write_text(json.dumps(result))
    before = _saved_files(cell.run_dir)
    with pytest.raises(AdapterError):
        campaign.run_campaign_cell(**failed_cell, retry_relay_startup=True)
    assert _saved_files(cell.run_dir) == before
    assert not (cell.run_dir.parent / ".relay-startup-failures").exists()


def test_failed_preflight_does_not_move_old_evidence(failed_cell, monkeypatch):
    def invalid(*args, **kwargs):
        raise AdapterError("data verification failed")
    monkeypatch.setattr(campaign, "validate_mle_protocol", invalid)
    before = _saved_files(failed_cell["cell"].run_dir)
    with pytest.raises(AdapterError, match="data verification failed"):
        campaign.run_campaign_cell(**failed_cell, retry_relay_startup=True)
    assert _saved_files(failed_cell["cell"].run_dir) == before


def test_wrong_clean_upstream_revision_does_not_move_old_evidence(failed_cell, monkeypatch):
    def wrong_revision(agent):
        raise AdapterError("upstream source revision differs from the pin")
    monkeypatch.setattr(campaign, "require_clean_upstream_source", wrong_revision)
    before = _saved_files(failed_cell["cell"].run_dir)
    with pytest.raises(AdapterError, match="differs from the pin"):
        campaign.run_campaign_cell(**failed_cell, retry_relay_startup=True)
    assert _saved_files(failed_cell["cell"].run_dir) == before
    assert not (failed_cell["cell"].run_dir.parent / ".relay-startup-failures").exists()


def test_concurrent_retry_cannot_touch_locked_cell(failed_cell):
    cell = failed_cell["cell"]
    before = _saved_files(cell.run_dir)
    with cell_execution_lock(cell.run_dir):
        with pytest.raises(AdapterError, match="already running"):
            campaign.run_campaign_cell(**failed_cell, retry_relay_startup=True)
    assert _saved_files(cell.run_dir) == before


def test_symlink_alias_cannot_bypass_cell_lock(failed_cell):
    cell = failed_cell["cell"]
    alias = cell.run_dir.parent / "alias"
    alias.symlink_to(cell.run_dir, target_is_directory=True)
    with cell_execution_lock(cell.run_dir):
        with pytest.raises(AdapterError, match="already running"):
            campaign.run_campaign_cell(
                **{**failed_cell, "cell": replace(cell, run_dir=alias)}, retry_relay_startup=True,
            )
    assert alias.is_symlink()
    assert (cell.run_dir / "result.json").is_file()


def test_socket_stays_short_when_tmpdir_is_a_deep_campaign_path(tmp_path, monkeypatch):
    deep = tmp_path / ("long-campaign-path-" * 8)
    deep.mkdir()
    monkeypatch.setenv("TMPDIR", str(deep))
    monkeypatch.setattr(tempfile, "tempdir", str(deep))
    monkeypatch.setattr(adapter, "check_proxy", lambda: None)
    monkeypatch.setattr(adapter, "_sample_hashes", lambda request: set())
    monkeypatch.setattr(adapter, "submission_roots", lambda request: (request.output_dir,))
    monkeypatch.setattr(adapter, "agent_download_proxy", lambda: nullcontext(None))
    sockets = []

    class LocalRelay:
        def __init__(self, **kwargs):
            self.path = kwargs["unix_socket"]
        def __enter__(self):
            self.socket = socket.socket(socket.AF_UNIX)
            self.socket.bind(str(self.path))
            sockets.append(self.path)
            return self
        def __exit__(self, *args):
            self.socket.close()

    monkeypatch.setattr(adapter, "RelayProcess", LocalRelay)
    request = adapter.MleLiteRequest(
        agent="ai-scientist", competition_id="aerial-cactus-identification",
        data_root=tmp_path, output_dir=deep / "output", model="test-model",
    )
    submission = request.output_dir / "submission.csv"
    command = CommandSpec(argv=("unused",), cwd=deep, artifact_path=submission)
    monkeypatch.setattr(adapter.MleLiteAdapter, "build_command", lambda *args, **kwargs: command)

    def execute(command, **kwargs):
        submission.write_text("id,target\n1,0.5\n")
        return CommandResult(command, 0, "")

    monkeypatch.setattr(adapter, "run_command", execute)
    assert adapter.MleLiteAdapter("ai-scientist").run(request) == submission
    assert sockets and len(str(sockets[0]).encode()) < 108
    assert not sockets[0].parent.exists()
