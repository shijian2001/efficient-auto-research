from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from arbor.coordinator.orchestrator import CoordinatorOrchestrator
from arbor.coordinator.idea_tree import IdeaTree, Node
from arbor.coordinator.tools.executor_run import (
    _check_tamper,
    _guard_protected,
    _run_after_executor_hook,
)
from arbor.coordinator.tools.git_ops import GitMergeBranchTool
from arbor.core.tools.path_guard import check_command_allowed, check_path_allowed
from arbor.mle.adapter import prepare_workspace
from arbor.mle.common import (
    AdapterError,
    discover_public_sample,
    parse_metric,
    sha256_file,
    validate_submission_remote,
)
from arbor.mle.eval_runner import run_candidate, verify_candidate
from arbor.mle.format_server import _multipart_file
from arbor.mle.run import recover_submission
from arbor.plugins.base import Plugin


def _public_task(tmp_path: Path, competition_id: str = "demo-task") -> Path:
    public = tmp_path / competition_id / "prepared" / "public"
    public.mkdir(parents=True)
    (public / "description.md").write_text("# Demo\n", encoding="utf-8")
    (public / "sample_submission.csv").write_text("id,target\n1,0\n", encoding="utf-8")
    return public


def test_parse_metric_uses_last_finite_value() -> None:
    assert parse_metric("METRIC=0.1\nwork\nMETRIC=-2.5e-3\n") == -0.0025


def test_discover_public_sample_extracts_zip(tmp_path: Path) -> None:
    public = tmp_path / "public"
    public.mkdir()
    archive = public / "en_sample_submission_2.csv.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("sample.csv", "id,target\n1,0\n")
    destination = tmp_path / "sample.csv"
    assert discover_public_sample(public, destination) == destination
    assert destination.read_text(encoding="utf-8") == "id,target\n1,0\n"


def test_prepare_workspace_builds_clean_git_project(tmp_path: Path) -> None:
    public = _public_task(tmp_path)
    spec = prepare_workspace(
        competition_id="demo-task",
        public_dir=public,
        description_path=public / "description.md",
        run_dir=tmp_path / "run",
        validation_url="http://127.0.0.1:9999",
        metric_direction="maximize",
        provider="openai-chat",
        model="demo-model",
        base_url="http://127.0.0.1:8000/v1",
        time_budget=3600,
    )
    assert spec.workspace.joinpath("input").is_symlink()
    assert not spec.workspace.joinpath("submission.csv").exists()
    assert spec.workspace.joinpath("eval.sh").stat().st_mode & 0o111
    config = yaml.safe_load(spec.research_config.read_text(encoding="utf-8"))
    assert config["plugin"] == "mle_adapter"
    assert config["llm"]["provider"] == "openai-chat"
    assert config["time_budget"] == 3600
    assert spec.run_dir.joinpath("submission.csv").is_file()
    plugin = yaml.safe_load(
        spec.workspace.joinpath("plugins/mle_adapter.yaml").read_text(encoding="utf-8")
    )
    assert plugin["eval_contract"]["metric_direction"] == "maximize"
    assert plugin["eval_contract"]["eval_cmd_test"].endswith("eval.sh verify")
    assert plugin["eval_contract"]["test_semantics"] == "artifact_verification_only"
    assert plugin["lifecycle_hooks"]["after_executor"]["require_verified_state"] is True
    assert ".mle/**" not in plugin["protected_paths"]
    assert not subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=spec.workspace, text=True
    ).strip()


def test_eval_runner_records_and_verifies_hashes(tmp_path: Path, monkeypatch) -> None:
    public = _public_task(tmp_path)
    spec = prepare_workspace(
        competition_id="demo-task",
        public_dir=public,
        description_path=public / "description.md",
        run_dir=tmp_path / "run",
        validation_url="http://validator",
        metric_direction="maximize",
    )
    monkeypatch.setattr("arbor.mle.eval_runner.validate_submission_remote", lambda *args: (True, "ok"))
    assert run_candidate(spec.workspace) == -1e30
    state_path = spec.workspace / "results" / "mle_eval_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["schema_version"] == 2
    assert state["submission_target_preexisting"] is False
    assert state["submission_created_after_cleanup"] is True
    assert state["solution_sha256"] == sha256_file(spec.workspace / "solution.py")
    assert state["submission_sha256"] == sha256_file(spec.workspace / "submission.csv")
    assert verify_candidate(spec.workspace) == -1e30
    spec.workspace.joinpath("submission.csv").write_text("id,target\n1,1\n", encoding="utf-8")
    with pytest.raises(AdapterError, match="changed after"):
        verify_candidate(spec.workspace)


def test_stale_submission_is_removed_before_failed_candidate(tmp_path: Path, monkeypatch) -> None:
    public = _public_task(tmp_path)
    spec = prepare_workspace(
        competition_id="demo-task",
        public_dir=public,
        description_path=public / "description.md",
        run_dir=tmp_path / "run",
        validation_url="http://validator",
        metric_direction="maximize",
    )
    submission = spec.workspace / "submission.csv"
    state = spec.workspace / "results" / "mle_eval_state.json"
    submission.write_text("id,target\n1,99\n", encoding="utf-8")
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text('{"schema_version": 1}\n', encoding="utf-8")
    (spec.workspace / "solution.py").write_text(
        "print('METRIC=0.999')\n",
        encoding="utf-8",
    )
    validation_calls: list[Path] = []

    def _unexpected_validation(*args):
        validation_calls.append(args[-1])
        return True, "ok"

    monkeypatch.setattr("arbor.mle.eval_runner.validate_submission_remote", _unexpected_validation)
    with pytest.raises(AdapterError, match="did not create"):
        run_candidate(spec.workspace)
    assert not submission.exists()
    assert not state.exists()
    assert validation_calls == []


def test_failed_second_step_cannot_reuse_first_submission(tmp_path: Path, monkeypatch) -> None:
    public = _public_task(tmp_path)
    spec = prepare_workspace(
        competition_id="demo-task",
        public_dir=public,
        description_path=public / "description.md",
        run_dir=tmp_path / "run",
        validation_url="http://validator",
        metric_direction="maximize",
    )
    monkeypatch.setattr("arbor.mle.eval_runner.validate_submission_remote", lambda *args: (True, "ok"))
    assert run_candidate(spec.workspace) == -1e30
    first_hash = sha256_file(spec.workspace / "submission.csv")
    (spec.workspace / "solution.py").write_text("print('METRIC=1.0')\n", encoding="utf-8")
    with pytest.raises(AdapterError, match="did not create"):
        run_candidate(spec.workspace)
    assert not (spec.workspace / "submission.csv").exists()
    assert not (spec.workspace / "results" / "mle_eval_state.json").exists()
    assert first_hash == sha256_file(spec.sample_submission)


def test_adapter_runtime_outputs_do_not_trip_protected_guard(tmp_path: Path, monkeypatch) -> None:
    public = _public_task(tmp_path)
    spec = prepare_workspace(
        competition_id="demo-task",
        public_dir=public,
        description_path=public / "description.md",
        run_dir=tmp_path / "run",
        validation_url="http://validator",
        metric_direction="maximize",
    )
    plugin_data = yaml.safe_load(
        (spec.workspace / "plugins" / "mle_adapter.yaml").read_text(encoding="utf-8")
    )
    plugin = Plugin(
        eval_contract=plugin_data["eval_contract"],
        protected_paths=plugin_data["protected_paths"],
        lifecycle_hooks=plugin_data["lifecycle_hooks"],
    )
    config = SimpleNamespace(enforce_protected=True, protected_paths=[], plugin=plugin)
    events: list[tuple[str, dict]] = []
    tree = SimpleNamespace(
        meta={},
        bus=SimpleNamespace(emit=lambda name, payload: events.append((name, payload))),
    )
    paths, manifest = _guard_protected(config, tree, spec.workspace)
    monkeypatch.setattr("arbor.mle.eval_runner.validate_submission_remote", lambda *args: (True, "ok"))
    run_candidate(spec.workspace)
    changes = _check_tamper(config, tree, spec.workspace, paths, manifest, "1", "node-1")
    assert changes == []
    assert events == []


def test_after_executor_snapshots_only_hash_bound_artifacts(tmp_path: Path, monkeypatch) -> None:
    public = _public_task(tmp_path)
    spec = prepare_workspace(
        competition_id="demo-task",
        public_dir=public,
        description_path=public / "description.md",
        run_dir=tmp_path / "run",
        validation_url="http://validator",
        metric_direction="maximize",
    )
    monkeypatch.setattr("arbor.mle.eval_runner.validate_submission_remote", lambda *args: (True, "ok"))
    run_candidate(spec.workspace)
    plugin = Plugin(
        eval_contract={
            "submission_path": "submission.csv",
            "solution_path": "solution.py",
            "evaluation_state_path": "results/mle_eval_state.json",
        },
        lifecycle_hooks={"after_executor": {"require_verified_state": True}},
    )
    config = SimpleNamespace(
        plugin=plugin,
        workspace_dir=str(spec.session_dir),
        cwd=str(spec.workspace),
    )
    asyncio.run(_run_after_executor_hook(config, spec.workspace, "1"))
    snapshot = spec.session_dir / "submissions" / "1.csv"
    manifest_path = spec.session_dir / "submissions" / "1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert snapshot.is_file()
    assert manifest["competition_id"] == "demo-task"
    assert manifest["submission_sha256"] == sha256_file(snapshot)
    assert manifest["solution_sha256"] == sha256_file(spec.workspace / "solution.py")

    (spec.workspace / "solution.py").write_text("print('changed')\n", encoding="utf-8")
    asyncio.run(_run_after_executor_hook(config, spec.workspace, "2"))
    assert not (spec.session_dir / "submissions" / "2.csv").exists()
    assert not (spec.session_dir / "submissions" / "2.json").exists()


def test_recovery_uses_only_verified_controller_snapshot(tmp_path: Path, monkeypatch) -> None:
    public = _public_task(tmp_path)
    spec = prepare_workspace(
        competition_id="demo-task",
        public_dir=public,
        description_path=public / "description.md",
        run_dir=tmp_path / "run",
        validation_url="http://validator",
        metric_direction="maximize",
    )
    monkeypatch.setattr("arbor.mle.eval_runner.validate_submission_remote", lambda *args: (True, "ok"))
    run_candidate(spec.workspace)
    plugin = Plugin(
        eval_contract={
            "submission_path": "submission.csv",
            "solution_path": "solution.py",
            "evaluation_state_path": "results/mle_eval_state.json",
        },
        lifecycle_hooks={"after_executor": {"require_verified_state": True}},
    )
    config = SimpleNamespace(
        plugin=plugin,
        workspace_dir=str(spec.session_dir),
        cwd=str(spec.workspace),
    )
    asyncio.run(_run_after_executor_hook(config, spec.workspace, "1"))
    tree_path = spec.session_dir / ".coordinator" / "idea_tree.json"
    tree_path.parent.mkdir(parents=True, exist_ok=True)
    tree_path.write_text(
        json.dumps(
            {
                "meta": {"metric_direction": "maximize"},
                "nodes": {"1": {"status": "done", "score": -1e30}},
            }
        ),
        encoding="utf-8",
    )
    # Force recovery through the controller snapshot rather than the mutable
    # trunk workspace artifact.
    (spec.workspace / "submission.csv").unlink()
    monkeypatch.setattr("arbor.mle.run.validate_submission_remote", lambda *args: (True, "ok"))
    output = recover_submission(spec)
    manifest = json.loads((spec.run_dir / "submission_manifest.json").read_text(encoding="utf-8"))
    assert output.is_file()
    assert manifest["source_kind"] == "verified_snapshot"
    assert manifest["source_node_id"] == "1"
    assert manifest["fallback"] is False
    assert manifest["sha256"] == sha256_file(output)


def test_recovery_rejects_snapshot_metric_not_matching_tree(tmp_path: Path, monkeypatch) -> None:
    public = _public_task(tmp_path)
    spec = prepare_workspace(
        competition_id="demo-task",
        public_dir=public,
        description_path=public / "description.md",
        run_dir=tmp_path / "run",
        validation_url="http://validator",
        metric_direction="maximize",
    )
    monkeypatch.setattr("arbor.mle.eval_runner.validate_submission_remote", lambda *args: (True, "ok"))
    run_candidate(spec.workspace)
    plugin = Plugin(
        eval_contract={
            "submission_path": "submission.csv",
            "solution_path": "solution.py",
            "evaluation_state_path": "results/mle_eval_state.json",
        },
        lifecycle_hooks={"after_executor": {"require_verified_state": True}},
    )
    config = SimpleNamespace(
        plugin=plugin,
        workspace_dir=str(spec.session_dir),
        cwd=str(spec.workspace),
    )
    asyncio.run(_run_after_executor_hook(config, spec.workspace, "1"))
    tree_path = spec.session_dir / ".coordinator" / "idea_tree.json"
    tree_path.parent.mkdir(parents=True, exist_ok=True)
    tree_path.write_text(
        json.dumps(
            {
                "meta": {"metric_direction": "maximize"},
                "nodes": {"1": {"status": "done", "score": 123.0}},
            }
        ),
        encoding="utf-8",
    )
    (spec.workspace / "submission.csv").unlink()
    with pytest.raises(AdapterError, match="no verified final submission"):
        recover_submission(spec)


def test_recovery_failure_preserves_emergency_fallback(tmp_path: Path) -> None:
    public = _public_task(tmp_path)
    spec = prepare_workspace(
        competition_id="demo-task",
        public_dir=public,
        description_path=public / "description.md",
        run_dir=tmp_path / "run",
        validation_url="http://validator",
        metric_direction="maximize",
    )
    fallback = spec.run_dir / "submission.csv"
    fallback_hash = sha256_file(fallback)
    with pytest.raises(AdapterError, match="no verified final submission"):
        recover_submission(spec)
    manifest = json.loads((spec.run_dir / "submission_manifest.json").read_text(encoding="utf-8"))
    assert sha256_file(fallback) == fallback_hash
    assert manifest["fallback"] is True


def test_artifact_verification_merge_is_not_recorded_as_test_score(
    tmp_path: Path, monkeypatch
) -> None:
    tree = IdeaTree(Node(id="ROOT", parent_id=None))
    node = Node(
        id="1",
        parent_id="ROOT",
        depth=1,
        status="done",
        score=0.8,
        code_ref="exp-1",
    )
    tree.add_node(node)
    tree.meta.update(
        {
            "metric_direction": "maximize",
            "baseline_score": 0.5,
            "trunk_score": 0.5,
            "eval_cmd_test": "verify",
            "test_semantics": "artifact_verification_only",
        }
    )
    config = SimpleNamespace(
        trunk_branch="trunk",
        eval_timeout=30,
        eval_retries=0,
        eval_retry_base_delay=0,
        eval_retry_max_delay=0,
        merge_threshold=0,
        plugin=None,
    )

    async def _verified(**kwargs):
        assert kwargs["evaluation_label"] == "artifact verification"
        return (
            0.8,
            "ok",
            {
                "score": 0.8,
                "evaluation_role": "artifact_verification_only",
                "official_grading": False,
            },
        )

    async def _git(command: str, cwd: str, timeout: int = 60):
        if command == "git branch --show-current":
            return "working", 0
        if command == "git rev-parse --short HEAD":
            return "deadbee", 0
        if command == "git log --oneline -3":
            return "deadbee merge", 0
        return "", 0

    monkeypatch.setattr("arbor.coordinator.tools.git_ops._run_eval_in_worktree", _verified)
    monkeypatch.setattr("arbor.coordinator.tools.git_ops._run_git", _git)
    tool = GitMergeBranchTool(
        cwd=str(tmp_path),
        config=config,
        tree=tree,
        provider=SimpleNamespace(),
    )
    result = asyncio.run(
        tool.execute(source_branch="exp-1", target_branch="trunk", node_id="1")
    )
    assert "Official/test score: not run" in result
    assert tree.get_node("1").status == "merged"
    assert tree.get_node("1").test_score is None
    assert tree.meta["trunk_score"] == 0.8
    assert tree.meta["test_trunk_score"] is None


def test_artifact_verification_rejects_metric_not_bound_to_node(tmp_path: Path, monkeypatch) -> None:
    tree = IdeaTree(Node(id="ROOT", parent_id=None))
    tree.add_node(Node(id="1", parent_id="ROOT", depth=1, status="done", score=0.7))
    tree.meta.update(
        {
            "metric_direction": "maximize",
            "eval_cmd_test": "verify",
            "test_semantics": "artifact_verification_only",
        }
    )
    config = SimpleNamespace(
        trunk_branch="trunk",
        eval_timeout=30,
        eval_retries=0,
        eval_retry_base_delay=0,
        eval_retry_max_delay=0,
        merge_threshold=0,
        plugin=None,
    )

    async def _mismatch(**kwargs):
        return (
            0.8,
            "ok",
            {
                "score": 0.8,
                "evaluation_role": "artifact_verification_only",
                "official_grading": False,
            },
        )

    monkeypatch.setattr("arbor.coordinator.tools.git_ops._run_eval_in_worktree", _mismatch)
    tool = GitMergeBranchTool(
        cwd=str(tmp_path),
        config=config,
        tree=tree,
        provider=SimpleNamespace(),
    )
    result = asyncio.run(
        tool.execute(source_branch="exp-1", target_branch="trunk", node_id="1")
    )
    assert "does not match" in result
    assert tree.get_node("1").status == "done"


def test_eval_contract_applies_test_command_and_dataset_info() -> None:
    saved = []
    orchestrator = CoordinatorOrchestrator.__new__(CoordinatorOrchestrator)
    orchestrator.config = SimpleNamespace(
        plugin=Plugin(
            name="demo",
            eval_contract={
                "metric_direction": "minimize",
                "eval_cmd": "dev",
                "eval_cmd_test": "test",
                "test_semantics": "artifact_verification_only",
                "dataset_info": "public only",
            },
        )
    )
    orchestrator.tree = SimpleNamespace(
        meta={"metric_direction": "maximize", "test_semantics": "independent_test"},
        save=lambda: saved.append(True),
    )
    orchestrator._apply_eval_contract()
    assert orchestrator.tree.meta["eval_cmd_test"] == "test"
    assert orchestrator.tree.meta["metric_direction"] == "minimize"
    assert orchestrator.tree.meta["test_semantics"] == "artifact_verification_only"
    assert orchestrator.tree.meta["dataset_info"] == "public only"
    assert saved == [True]


def test_private_path_guard_matches_official_and_local_layouts() -> None:
    assert check_path_allowed("/private/data/demo/answers.csv")
    assert check_path_allowed("/mnt/data/demo/prepared/private/answers.csv")
    assert check_command_allowed("cat /mnt/data/demo/prepared/private/answers.csv")
    assert check_path_allowed("/mnt/data/demo/prepared/public/train.csv") is None


def test_remote_validation_protocol_returns_only_validity(tmp_path: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            assert self.headers["X-Competition-Id"] == "demo-task"
            self.rfile.read(int(self.headers["Content-Length"]))
            body = json.dumps({"valid": True, "message": "valid"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    submission = tmp_path / "submission.csv"
    submission.write_text("id,target\n1,0\n", encoding="utf-8")
    try:
        assert validate_submission_remote(
            f"http://127.0.0.1:{server.server_port}", "demo-task", submission
        ) == (True, "valid")
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_multipart_compatibility_with_mlevolve_client() -> None:
    boundary = "demo-boundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="submission.csv"\r\n'
        "Content-Type: text/csv\r\n\r\n"
        "id,target\n1,0\n"
        f"\r\n--{boundary}--\r\n"
    ).encode()
    assert _multipart_file(body, f"multipart/form-data; boundary={boundary}") == b"id,target\n1,0\n"
