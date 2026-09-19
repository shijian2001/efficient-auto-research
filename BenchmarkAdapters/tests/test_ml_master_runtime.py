from __future__ import annotations

import json
import os
import signal
import sys
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
