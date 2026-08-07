from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from agent_adapters.shared.harbor_shell import (
    HarborBridgeCancelled,
    HarborShellBridge,
    run_sync_with_cancellation,
)


class _Result:
    def __init__(self, stdout="", stderr="", return_code=0):
        self.stdout = stdout
        self.stderr = stderr
        self.return_code = return_code


class _Environment:
    def __init__(self, root: Path):
        self.root = root
        self.calls = []

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        self.calls.append((command, cwd, env, timeout_sec, user))
        return _Result(stdout="/app\n" if command == "pwd" else "ok", return_code=0)

    async def upload_file(self, source_path, target_path):
        target = self.root / target_path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(source_path).read_bytes())

    async def download_file(self, source_path, target_path):
        Path(target_path).write_bytes((self.root / source_path.lstrip("/")).read_bytes())


def test_bridge_runs_sync_operations_on_harbor_loop(tmp_path: Path) -> None:
    async def scenario() -> None:
        environment = _Environment(tmp_path)
        bridge = HarborShellBridge(environment, loop=asyncio.get_running_loop(), cwd="/root")

        def worker():
            bridge.write_file("/root/demo.txt", "hello")
            assert bridge.read_file("/root/demo.txt") == "hello"
            return bridge.run("printf ok", timeout=7)

        result = await asyncio.to_thread(worker)
        assert result.stdout == "ok"
        assert environment.calls[-1][1] == "/root"
        assert environment.calls[-1][3] == 7

    asyncio.run(scenario())


def test_bridge_resolves_relative_files_against_container_workdir(tmp_path: Path) -> None:
    async def scenario() -> None:
        environment = _Environment(tmp_path)
        bridge = HarborShellBridge(environment, loop=asyncio.get_running_loop())

        def worker() -> None:
            bridge.write_file("demo.txt", "hello")
            assert bridge.read_file("demo.txt") == "hello"

        await asyncio.to_thread(worker)
        assert (tmp_path / "app" / "demo.txt").read_text() == "hello"
        assert environment.calls[0][0] == "pwd"

    asyncio.run(scenario())


def test_sync_runner_sets_cancel_event() -> None:
    async def scenario() -> None:
        event = threading.Event()
        stopped = threading.Event()

        def worker():
            event.wait(2)
            stopped.set()
            return "stopped"

        task = asyncio.create_task(run_sync_with_cancellation(worker, cancel_event=event))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert event.is_set()
        assert stopped.is_set()

    asyncio.run(scenario())


def test_sync_runner_preserves_cancellation_when_worker_raises() -> None:
    async def scenario() -> None:
        event = threading.Event()

        def worker():
            event.wait(2)
            raise HarborBridgeCancelled("stopped")

        task = asyncio.create_task(run_sync_with_cancellation(worker, cancel_event=event))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_bridge_checks_cancelled_event(tmp_path: Path) -> None:
    event = threading.Event()
    event.set()
    loop = asyncio.new_event_loop()
    try:
        bridge = HarborShellBridge(_Environment(tmp_path), loop=loop, cancel_event=event)
        with pytest.raises(HarborBridgeCancelled):
            bridge.check_cancelled()
    finally:
        loop.close()


def test_bridge_rejects_stopped_event_loop_without_leaking_coroutine(tmp_path: Path) -> None:
    loop = asyncio.new_event_loop()
    try:
        bridge = HarborShellBridge(_Environment(tmp_path), loop=loop)
        with pytest.raises(RuntimeError, match="event loop is not running"):
            bridge.run("blocked")
    finally:
        loop.close()


def test_bridge_cancellation_before_coroutine_start_does_not_hang(
    tmp_path: Path,
    monkeypatch,
) -> None:
    loop = asyncio.new_event_loop()
    loop_blocked = threading.Event()
    release_loop = threading.Event()
    loop_drained = threading.Event()

    def block_loop() -> None:
        loop_blocked.set()
        release_loop.wait(2)

    def loop_worker() -> None:
        asyncio.set_event_loop(loop)
        loop.call_soon(block_loop)
        loop.run_forever()

    loop_thread = threading.Thread(target=loop_worker)
    loop_thread.start()
    assert loop_blocked.wait(1)

    submitted = threading.Event()
    original_submit = asyncio.run_coroutine_threadsafe

    def tracked_submit(coroutine, target_loop):
        future = original_submit(coroutine, target_loop)
        submitted.set()
        return future

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", tracked_submit)

    cancel_event = threading.Event()
    bridge = HarborShellBridge(
        _Environment(tmp_path),
        loop=loop,
        cancel_event=cancel_event,
    )
    captured: list[BaseException] = []

    def bridge_worker() -> None:
        try:
            bridge.run("blocked")
        except BaseException as exc:
            captured.append(exc)

    worker = threading.Thread(target=bridge_worker)
    worker.start()
    assert submitted.wait(1)
    cancel_event.set()
    worker.join(1)
    assert not worker.is_alive()
    assert isinstance(captured[0], HarborBridgeCancelled)

    release_loop.set()
    loop.call_soon_threadsafe(loop_drained.set)
    assert loop_drained.wait(1)
    loop.call_soon_threadsafe(loop.stop)
    loop_thread.join(1)
    loop.close()


def test_bridge_cancels_inflight_environment_operation(tmp_path: Path) -> None:
    class BlockingEnvironment(_Environment):
        def __init__(self, root: Path):
            super().__init__(root)
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            del command, cwd, env, timeout_sec, user
            self.started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

    async def scenario() -> None:
        event = threading.Event()
        environment = BlockingEnvironment(tmp_path)
        bridge = HarborShellBridge(
            environment,
            loop=asyncio.get_running_loop(),
            cancel_event=event,
        )
        worker = asyncio.create_task(asyncio.to_thread(bridge.run, "blocked"))
        await environment.started.wait()
        event.set()
        with pytest.raises(HarborBridgeCancelled):
            await worker
        await asyncio.wait_for(environment.cancelled.wait(), timeout=1)

    asyncio.run(scenario())


def test_bridge_propagates_environment_timeout_error(tmp_path: Path) -> None:
    class TimeoutEnvironment(_Environment):
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            del command, cwd, env, timeout_sec, user
            raise TimeoutError("environment timeout")

    async def scenario() -> None:
        bridge = HarborShellBridge(
            TimeoutEnvironment(tmp_path),
            loop=asyncio.get_running_loop(),
        )
        with pytest.raises(TimeoutError, match="environment timeout"):
            await asyncio.to_thread(bridge.run, "timeout")

    asyncio.run(scenario())


def test_bridge_mkdir_uses_container_workdir(tmp_path: Path) -> None:
    class MkdirEnvironment(_Environment):
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            del cwd, env, timeout_sec, user
            self.calls.append((command, None, None, None, None))
            if command == "pwd":
                return _Result(stdout="/app\n")
            return _Result()

    async def scenario() -> None:
        environment = MkdirEnvironment(tmp_path)
        bridge = HarborShellBridge(environment, loop=asyncio.get_running_loop())
        await asyncio.to_thread(bridge.mkdir, "nested")
        assert environment.calls[-1][0] == "mkdir -p /app/nested"

    asyncio.run(scenario())
