from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from BenchmarkAdapters.MLEBenchLite.runtime_monitor import (
    RuntimeMonitor,
    discover_api_events,
    discover_candidates,
    discover_native_heartbeats,
    observe_process,
)


def _heartbeat(root: Path, **changes):
    monitor = root / "agent-output/runtime-monitor"
    monitor.mkdir(parents=True)
    data = {
        "candidate_id": "abc", "pid": 99999999, "pgid": 99999999, "process_start_ticks": 1,
        "argv": ["python", "train.py"], "started_at": 0, "last_output_at": 0,
        "deadline": 9999999999, "state": "running", "stdout_path": "stdout.log",
        "exit_code": None, "auto_actions": [],
    }
    data.update(changes)
    (monitor / "candidate-abc.json").write_text(json.dumps(data), encoding="utf-8")
    return data


def test_malformed_heartbeat_is_reported_and_status_is_atomic(tmp_path, monkeypatch):
    d = tmp_path / "campaign"
    path = d / "agent-output/runtime-monitor"
    path.mkdir(parents=True)
    (path / "candidate-bad.json").write_text('{"pid": 3}', encoding="utf-8")
    monkeypatch.setattr("BenchmarkAdapters.MLEBenchLite.runtime_monitor.shutil.disk_usage", lambda _: type("D", (), {"free": 20 * 1024**3, "used": 0, "total": 20 * 1024**3})())
    status = RuntimeMonitor(d, now=lambda: 1000).poll()
    assert status["summary"]["malformed"] == 1
    assert status["summary"]["major_incidents"] == 1
    saved = json.loads((path / "status.json").read_text())
    assert saved["schema_version"] == 1
    assert json.loads((path / "incidents.jsonl").read_text().splitlines()[0])["code"] == "malformed_heartbeat"


def test_identity_mismatch_never_treats_reused_pid_as_ours(tmp_path):
    d = tmp_path / "campaign"
    _heartbeat(d, pid=os.getpid(), pgid=os.getpgid(0), process_start_ticks=0, last_output_at=999)
    status = RuntimeMonitor(d, now=lambda: 1000).poll()
    row = status["candidates"][0]
    assert row["process_present"] is True
    assert row["identity_ok"] is False
    assert any(i["code"] == "identity_mismatch" and i["severity"] == "major" for i in status["incidents"])


def test_stale_process_with_cpu_activity_is_not_major(tmp_path, monkeypatch):
    d = tmp_path / "campaign"
    _heartbeat(d, pid=42, pgid=42, process_start_ticks=7, last_output_at=0)
    seq = iter([
        type("O", (), {"present": True, "identity_ok": True, "state": "R", "pgid": 42, "argv": (), "cpu_ticks": 10, "gpu_active": False, "reason": None})(),
        type("O", (), {"present": True, "identity_ok": True, "state": "R", "pgid": 42, "argv": (), "cpu_ticks": 12, "gpu_active": False, "reason": None})(),
    ])
    monkeypatch.setattr("BenchmarkAdapters.MLEBenchLite.runtime_monitor.observe_process", lambda *a, **k: next(seq))
    first = RuntimeMonitor(d, now=lambda: 1000)
    first.poll()
    status = first.poll()
    assert not any(i["code"] == "no_activity" for i in status["incidents"])


def test_repeated_oom_and_low_disk_are_major(tmp_path, monkeypatch):
    d = tmp_path / "campaign"
    _heartbeat(d, stdout_path="one.log", state="failed", failure_reason="CUDA out of memory")
    (d / "one.log").write_text("out of memory", encoding="utf-8")
    monitor = d / "agent-output/runtime-monitor"
    data = json.loads((monitor / "candidate-abc.json").read_text())
    data["candidate_id"] = "def"
    data["stdout_path"] = "two.log"
    (monitor / "candidate-def.json").write_text(json.dumps(data), encoding="utf-8")
    (d / "two.log").write_text("OOM-kill", encoding="utf-8")
    monkeypatch.setattr("BenchmarkAdapters.MLEBenchLite.runtime_monitor.shutil.disk_usage", lambda _: type("D", (), {"free": 100, "used": 1, "total": 100})())
    status = RuntimeMonitor(d, now=lambda: 1000).poll()
    codes = {i["code"] for i in status["incidents"]}
    assert "repeated_oom" in codes
    assert "low_disk" in codes


def test_same_oom_text_is_not_counted_again_on_every_poll(tmp_path, monkeypatch):
    d = tmp_path / "campaign"
    _heartbeat(d, state="failed", failure_reason="CUDA out of memory")
    monkeypatch.setattr(
        "BenchmarkAdapters.MLEBenchLite.runtime_monitor.shutil.disk_usage",
        lambda _: type("D", (), {"free": 20 * 1024**3, "used": 0, "total": 20 * 1024**3})(),
    )
    monitor = RuntimeMonitor(d, now=lambda: 1000)
    first = monitor.poll()
    second = monitor.poll()
    assert any(item["code"] == "oom" for item in first["incidents"])
    assert not any(item["code"] == "repeated_oom" for item in second["incidents"])


def test_cross_namespace_requires_a_fresh_heartbeat(monkeypatch):
    data = {
        "pid": 7,
        "pgid": 7,
        "process_start_ticks": 1,
        "pid_namespace": "pid:[candidate]",
        "state": "running",
        "updated_at": 0,
        "last_output_at": 0,
        "started_at": 0,
        "deadline": 9999,
    }
    monkeypatch.setattr(
        "BenchmarkAdapters.MLEBenchLite.runtime_monitor.os.readlink",
        lambda _: "pid:[host]",
    )
    observed = observe_process(data, now=100)
    assert observed.present is False
    assert observed.identity_ok is False
    assert observed.reason == "isolated pid namespace heartbeat stale"


def test_stderr_is_scanned_for_cuda_failures(tmp_path, monkeypatch):
    d = tmp_path / "campaign"
    stderr = d / "stderr.log"
    d.mkdir()
    stderr.write_text("CUDA error: device-side assert triggered\n", encoding="utf-8")
    _heartbeat(d, state="failed", stderr_path=str(stderr), stdout_path="missing.log")
    monkeypatch.setattr(
        "BenchmarkAdapters.MLEBenchLite.runtime_monitor.shutil.disk_usage",
        lambda _: type("D", (), {"free": 20 * 1024**3, "used": 0, "total": 20 * 1024**3})(),
    )
    status = RuntimeMonitor(d, now=lambda: 1000).poll()
    assert any(item["code"] == "cuda_failure" for item in status["incidents"])


def test_stale_native_heartbeat_is_a_major_incident(tmp_path, monkeypatch):
    d = tmp_path / "campaign"
    heartbeat = d / "cell/agent-output/runtime-monitor/native-heartbeat.json"
    heartbeat.parent.mkdir(parents=True)
    heartbeat.write_text(
        json.dumps(
            {
                "state": "running",
                "updated_at": 0,
                "deadline": 9999,
                "task_id": "demo",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "BenchmarkAdapters.MLEBenchLite.runtime_monitor.shutil.disk_usage",
        lambda _: type("D", (), {"free": 20 * 1024**3, "used": 0, "total": 20 * 1024**3})(),
    )
    status = RuntimeMonitor(d, now=lambda: 1000).poll()
    assert any(item["code"] == "native_heartbeat_stale" for item in status["incidents"])


def test_api_timeout_retries_are_deduplicated(tmp_path, monkeypatch):
    d = tmp_path / "campaign"
    relay = d / "cell/agent-output/relay.log"
    relay.parent.mkdir(parents=True)
    relay.write_text("chat.completions attempt 1/21 failed: timed out; retry in 3s\n", encoding="utf-8")
    monkeypatch.setattr(
        "BenchmarkAdapters.MLEBenchLite.runtime_monitor.shutil.disk_usage",
        lambda _: type("D", (), {"free": 20 * 1024**3, "used": 0, "total": 20 * 1024**3})(),
    )
    monitor = RuntimeMonitor(d, now=lambda: 1000)
    first = monitor.poll()
    second = monitor.poll()
    assert any(item["code"] == "api_timeout_retries" for item in first["incidents"])
    assert not any(item["code"] == "api_timeout_retries" for item in second["incidents"])


def test_consecutive_api_retries_escalate_to_major(tmp_path, monkeypatch):
    d = tmp_path / "campaign"
    relay = d / "cell/agent-output/relay.log"
    relay.parent.mkdir(parents=True)
    relay.write_text(
        "".join(
            f"2026-09-19 04:{index:02d}:00,000 [proxy] WARNING chat.completions attempt {index}/21 failed: timed out; retry in 3s\n"
            for index in range(1, 7)
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "BenchmarkAdapters.MLEBenchLite.runtime_monitor.shutil.disk_usage",
        lambda _: type("D", (), {"free": 20 * 1024**3, "used": 0, "total": 20 * 1024**3})(),
    )
    status = RuntimeMonitor(d, now=lambda: 1000).poll()
    incident = next(item for item in status["incidents"] if item["code"] == "api_timeout_retries")
    assert incident["severity"] == "major"
    assert "streak=6" in incident["detail"]


def test_discovery_avoids_scratch_and_survives_directory_removal(tmp_path, monkeypatch):
    root = tmp_path / "campaign"
    cell = root / "round-01/task/ml-master-2/seed-0/task"
    _heartbeat(cell, state="completed", exit_code=0)
    output = cell / "agent-output"
    native = output / "runtime-monitor/native-heartbeat.json"
    native.write_text(json.dumps({"state": "running", "updated_at": 1000}))
    relay = output / "relay.log"
    relay.write_text("chat.completions attempt 1/21 failed: timed out\n")
    scratch = output / "tmp/pymp-vanishing"
    scratch.mkdir(parents=True)
    # Directory churn outside agent-output must not hide a healthy sibling cell.
    disappearing = root / "removed-during-scan"
    disappearing.mkdir()
    scanned = []
    scandir = os.scandir

    def racing_scandir(path):
        directory = Path(path)
        scanned.append(directory)
        if directory == disappearing:
            directory.rmdir()
        if directory == scratch:
            directory.rmdir()
        return scandir(path)

    monkeypatch.setattr(os, "scandir", racing_scandir)
    candidates, errors = discover_candidates(root)
    assert [item.candidate_id for item in candidates] == ["abc"]
    assert errors == []
    assert [path for path, _ in discover_native_heartbeats(root)] == [native]
    assert [path for path, _, _ in discover_api_events(root)] == [relay]
    assert disappearing in scanned
    assert not disappearing.exists()
    assert scratch.exists()
    assert not any(output / name in scanned for name in ("tmp", "workspace", "native"))


def test_monitor_retries_after_status_write_failure(tmp_path, monkeypatch, caplog):
    from BenchmarkAdapters.MLEBenchLite import runtime_monitor as module

    _heartbeat(tmp_path, state="completed", exit_code=0)
    monkeypatch.setattr(module, "_gpu_pids", lambda: set())
    original = module._atomic_json
    writes = []

    def write_once_failing(path, value, **kwargs):
        writes.append(path)
        if len(writes) == 1:
            raise OSError("No space left on device")
        return original(path, value, **kwargs)

    monkeypatch.setattr(module, "_atomic_json", write_once_failing)
    monitor = RuntimeMonitor(tmp_path, durable_writes=False)
    assert monitor.poll_safely() is None
    assert "will retry without stopping the Agent" in caplog.text
    status = monitor.poll_safely()
    assert status["summary"]["completed"] == 1
    assert json.loads(monitor.status_path.read_text())["summary"]["completed"] == 1


@pytest.mark.parametrize("signal_error", [KeyboardInterrupt, SystemExit])
def test_safe_poll_preserves_cancellation(tmp_path, monkeypatch, signal_error):
    monitor = RuntimeMonitor(tmp_path)

    def cancelled():
        raise signal_error()

    monkeypatch.setattr(monitor, "poll", cancelled)
    with pytest.raises(signal_error):
        monitor.poll_safely()
