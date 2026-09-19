from __future__ import annotations

import json
import os
import signal
import shlex
import sys
import time
from pathlib import Path

import pytest

from BenchmarkAdapters.MLEBenchLite import ml_master_runtime as runtime
from BenchmarkAdapters.MLEBenchLite.runtime_monitor import RuntimeMonitor


@pytest.mark.parametrize(
    ("budget", "child_seconds", "child_exit", "expected_state", "expected_return"),
    [
        (10, 0.2, 0, "completed", 0),
        (10, 0.2, 7, "failed", 7),
        (0.2, 30, 0, "budget_expired", 0),
    ],
)
def test_monitor_failure_preserves_child_lifecycle(
    tmp_path, monkeypatch, caplog,
    budget, child_seconds, child_exit, expected_state, expected_return,
):
    """Monitoring may fail, but only the child/deadline should end research."""
    monkeypatch.setattr(runtime, "preflight", lambda: {})
    monkeypatch.setattr(os, "environ", os.environ.copy())
    sleep = runtime.time.sleep
    monkeypatch.setattr(runtime.time, "sleep", lambda seconds: sleep(min(seconds, 0.01)))
    polls = []

    def broken_monitor(self):
        polls.append(1)
        if len(polls) % 2:
            raise FileNotFoundError("tmp/pymp-removed-during-scan")
        raise RuntimeError("monitoring-only failure")

    monkeypatch.setattr(RuntimeMonitor, "poll", broken_monitor)
    marker = tmp_path / "child-completed"
    child = (
        "import pathlib, sys, time; "
        f"time.sleep({child_seconds!r}); "
        f"pathlib.Path({str(marker)!r}).write_text('completed'); "
        f"sys.exit({child_exit})"
    )
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        result = runtime.main([
            "--budget-seconds", str(budget),
            "--output-dir", str(tmp_path / "agent-output"),
            "--task-id", "monitor-regression",
            "--", sys.executable, "-c", child,
        ])
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    assert result == expected_return
    assert len(polls) >= 2  # Includes retries and the final poll during cleanup.
    assert "FileNotFoundError" in caplog.text
    assert "RuntimeError" in caplog.text
    heartbeat = json.loads(
        (tmp_path / "agent-output/runtime-monitor/native-heartbeat.json").read_text()
    )
    assert heartbeat["state"] == expected_state
    if expected_state == "budget_expired":
        assert not marker.exists()
        assert heartbeat["return_code"] < 0
    else:
        assert marker.read_text() == "completed"
        assert heartbeat["return_code"] == child_exit
    assert not Path(f"/proc/{heartbeat['pid']}").exists()


@pytest.mark.parametrize("legacy_cap", [None, "43110", "5400"])
def test_candidate_gets_remaining_budget_without_default_ninety_minute_cap(tmp_path, legacy_cap):
    environment = {**os.environ, "ML_MASTER_MONITOR_DIR": str(tmp_path),
                   "ML_MASTER_DEADLINE_EPOCH": str(time.time() + 43200),
                   "ML_MASTER_CANDIDATE_POLICY": "native"}
    environment.pop("ML_MASTER_CHILD_TIMEOUT_SECONDS", None)
    if legacy_cap:
        environment["ML_MASTER_CHILD_TIMEOUT_SECONDS"] = legacy_cap
    command = shlex.join([sys.executable, "-c", "import time; time.sleep(0.1)"])
    result = runtime.run_candidate(command, str(tmp_path), environment, 86400)
    assert result["exit_code"] == 0
    heartbeat = json.loads(next(tmp_path.glob("candidate-*.json")).read_text())
    allowance = heartbeat["deadline"] - heartbeat["started_at"]
    if legacy_cap == "5400":
        # Existing Cactus processes keep the configuration they launched with.
        assert allowance == 5400
    else:
        assert 43000 < allowance <= 43110


@pytest.mark.parametrize("native_timeout", [0.2, 86400])
def test_native_policy_ignores_error_text_but_enforces_real_time_limits(tmp_path, native_timeout):
    environment = {**os.environ, "ML_MASTER_MONITOR_DIR": str(tmp_path),
                   "ML_MASTER_DEADLINE_EPOCH": str(time.time() + 90.4),
                   "ML_MASTER_CANDIDATE_POLICY": "native"}
    environment.pop("ML_MASTER_CHILD_TIMEOUT_SECONDS", None)
    command = shlex.join([sys.executable, "-c",
                         "import time; print('Downloading: weights; AF_UNIX path too long', flush=True); time.sleep(30)"])
    result = runtime.run_candidate(command, str(tmp_path), environment, native_timeout)
    heartbeat = json.loads(next(tmp_path.glob("candidate-*.json")).read_text())
    assert result["exit_code"] == -1
    assert heartbeat["state"] == "timed_out"
    assert "Downloading:" in result["stdout"]
    assert "stop_candidate_at_deadline_and_return_to_debug" in heartbeat["auto_actions"]
    assert not any("offline" in action or "infrastructure_failure" in action
                   for action in heartbeat["auto_actions"])
    assert not Path(f"/proc/{heartbeat['pid']}").exists()
