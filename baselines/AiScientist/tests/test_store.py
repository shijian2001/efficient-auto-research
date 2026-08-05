from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from aisci_core.models import JobSpec, JobStatus, JobType, MLESpec, PaperSpec, RuntimeProfile, RunPhase
from aisci_core.paths import database_path, ensure_job_dirs, jobs_root, repo_root, resolve_job_paths, state_root, var_root
from aisci_core.runner import JobRunner
from aisci_core.store import JobStore


def test_store_create_list_and_events(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    paper = store.create_job(
        JobSpec(
            job_type=JobType.PAPER,
            objective="paper test",
            llm_profile="paper-default",
            runtime_profile=RuntimeProfile(),
            mode_spec=PaperSpec(paper_md_path="/tmp/paper.md"),
        )
    )
    mle = store.create_job(
        JobSpec(
            job_type=JobType.MLE,
            objective="mle test",
            llm_profile="mle-default",
            runtime_profile=RuntimeProfile(),
            mode_spec=MLESpec(workspace_bundle_zip="/tmp/workspace.zip"),
        )
    )
    assert store.get_job(paper.id).job_type == JobType.PAPER
    assert len(store.list_jobs()) == 2
    event = store.append_event(paper.id, "status", RunPhase.INGEST, "created")
    events = store.list_events(paper.id)
    assert event.id == events[0].id
    assert store.get_job(mle.id).mode_spec.workspace_bundle_zip == "/tmp/workspace.zip"
    assert store.get_job(paper.id).mode_spec.enable_online_research is True
    assert re.match(r"^\d{8}-\d{6}-[0-9a-f]{8}$", paper.id)


def test_runner_ingests_conversation_jsonl(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AISCI_REPO_ROOT", str(tmp_path))
    store = JobStore(tmp_path / "jobs.db")
    runner = JobRunner(store=store)
    job = store.create_job(
        JobSpec(
            job_type=JobType.PAPER,
            objective="paper test",
            llm_profile="paper-default",
            runtime_profile=RuntimeProfile(),
            mode_spec=PaperSpec(paper_md_path="/tmp/paper.md"),
        )
    )
    paths = ensure_job_dirs(resolve_job_paths(job.id))
    conversation = [
        {
            "ts": 1.0,
            "event": "model_response",
            "text": "Read the paper and create a plan.",
            "tool_calls": [{"name": "read_paper"}],
        },
        {
            "ts": 2.0,
            "event": "tool_result",
            "tool": "prioritize_tasks",
            "result_preview": "wrote prioritized_tasks.md",
        },
    ]
    with (paths.logs_dir / "conversation.jsonl").open("w", encoding="utf-8") as handle:
        for row in conversation:
            handle.write(json.dumps(row) + "\n")

    runner._ingest_conversation_events(job.id, paths.logs_dir / "conversation.jsonl")

    events = store.list_events(job.id)
    assert any(event.event_type == "model_response" for event in events)
    assert any(event.event_type == "tool_result" for event in events)
    assert any(event.phase == RunPhase.ANALYZE for event in events)
    assert any(event.phase == RunPhase.PRIORITIZE for event in events)


def test_output_root_can_be_separated_from_repo_root(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    output = tmp_path / "runtime"
    monkeypatch.setenv("AISCI_REPO_ROOT", str(repo))
    monkeypatch.setenv("AISCI_OUTPUT_ROOT", str(output))

    assert repo_root() == repo.resolve()
    assert var_root() == output.resolve()
    assert jobs_root() == output.resolve() / "jobs"
    assert state_root() == output.resolve() / ".aisci"
    assert database_path() == output.resolve() / ".aisci" / "state" / "jobs.db"


def test_store_reconciles_stale_running_jobs(tmp_path: Path, monkeypatch) -> None:
    store = JobStore(tmp_path / "jobs.db")
    job = store.create_job(
        JobSpec(
            job_type=JobType.PAPER,
            objective="paper test",
            llm_profile="paper-default",
            runtime_profile=RuntimeProfile(),
            mode_spec=PaperSpec(paper_md_path="/tmp/paper.md"),
        )
    )
    store.mark_running(job.id, 424242)
    monkeypatch.setattr(
        "aisci_core.store.os.kill",
        lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()) if pid == 424242 else None,
    )

    reconciled = store.get_job(job.id)

    assert reconciled.status == JobStatus.FAILED
    assert reconciled.error == "worker exited unexpectedly before final status update"
    events = store.list_events(job.id)
    stale_events = [event for event in events if event.payload.get("reason") == "stale_worker"]
    assert len(stale_events) == 1
    assert stale_events[0].payload["worker_pid"] == 424242
    assert store.list_jobs()[0].status == JobStatus.FAILED


def test_legacy_paper_inputs_deserialize_as_read_only_spec() -> None:
    spec = PaperSpec.model_validate({"pdf_path": "/tmp/paper.pdf"})

    assert spec.pdf_path == "/tmp/paper.pdf"
    assert spec.uses_legacy_inputs is True
    assert spec.legacy_input_fields == ("pdf_path",)


def test_store_reads_legacy_paper_rows_without_crashing(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    now = datetime.now().astimezone().isoformat()
    runtime_profile = RuntimeProfile().model_dump_json()
    legacy_mode_spec = json.dumps({"pdf_path": "/tmp/paper.pdf"})

    with store.connect() as conn:
        conn.execute(
            """
            insert into jobs (
                id, job_type, status, phase, objective, llm_profile,
                runtime_profile_json, mode_spec_json, created_at, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-paper-job",
                JobType.PAPER.value,
                JobStatus.SUCCEEDED.value,
                RunPhase.FINALIZE.value,
                "legacy paper",
                "paper-default",
                runtime_profile,
                legacy_mode_spec,
                now,
                now,
            ),
        )

    job = store.get_job("legacy-paper-job")
    listed_jobs = store.list_jobs()

    assert job.mode_spec.pdf_path == "/tmp/paper.pdf"
    assert job.mode_spec.uses_legacy_inputs is True
    assert [item.id for item in listed_jobs] == ["legacy-paper-job"]


def test_store_rejects_new_legacy_paper_jobs(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")

    with pytest.raises(ValueError, match="deprecated inputs \\(pdf_path\\)"):
        store.create_job(
            JobSpec(
                job_type=JobType.PAPER,
                objective="legacy paper",
                llm_profile="paper-default",
                runtime_profile=RuntimeProfile(),
                mode_spec=PaperSpec(pdf_path="/tmp/paper.pdf"),
            )
        )
