"""Synchronous facade over Harbor's asynchronous BaseEnvironment API."""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import math
import os
import shlex
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, TypeVar


T = TypeVar("T")


class HarborBridgeCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class HarborCommandResult:
    stdout: str
    stderr: str
    return_code: int

    @property
    def returncode(self) -> int:
        return self.return_code

    @property
    def exit_code(self) -> int:
        return self.return_code


class HarborShellBridge:
    """Make one Harbor task environment usable by synchronous native Agent loops."""

    def __init__(
        self,
        environment: Any,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
        cwd: str | None = None,
        deadline: float | None = None,
        cancel_event: threading.Event | None = None,
        output_limit: int = 30000,
    ) -> None:
        self.environment = environment
        self.loop = loop or asyncio.get_running_loop()
        self.cwd = cwd
        self.deadline = deadline
        self.cancel_event = cancel_event or threading.Event()
        self.output_limit = output_limit
        self._resolved_cwd: str | None = cwd
        self._cwd_lock = threading.Lock()

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise HarborBridgeCancelled("Harbor Agent execution was cancelled")
        if self.deadline is not None and time.monotonic() >= self.deadline:
            self.cancel_event.set()
            raise TimeoutError("Harbor Agent deadline reached")

    def _remaining_timeout(self, requested: int | float | None) -> float | None:
        self.check_cancelled()
        timeout = float(requested) if requested is not None else None
        if self.deadline is not None:
            remaining = max(0.0, self.deadline - time.monotonic())
            timeout = remaining if timeout is None else min(timeout, remaining)
        return timeout

    def _cancel_future(
        self,
        future: concurrent.futures.Future[Any],
        completed: threading.Event,
    ) -> None:
        future.cancel()
        completed.wait()

    def _submit(self, awaitable, timeout: int | float | None = None):
        try:
            self.check_cancelled()
        except Exception:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise
        wait_timeout = self._remaining_timeout(timeout)
        started = threading.Event()
        completed = threading.Event()

        async def tracked_operation():
            started.set()
            try:
                return await awaitable
            finally:
                completed.set()

        tracked = tracked_operation()
        try:
            if not self.loop.is_running():
                raise RuntimeError("Harbor event loop is not running")
            future = asyncio.run_coroutine_threadsafe(tracked, self.loop)
        except Exception:
            tracked.close()
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise

        def operation_done(_future: concurrent.futures.Future[Any]) -> None:
            if not started.is_set():
                close = getattr(awaitable, "close", None)
                if callable(close):
                    close()
                completed.set()

        future.add_done_callback(operation_done)
        started_at = time.monotonic()
        try:
            while True:
                self.check_cancelled()
                if wait_timeout is not None:
                    remaining = wait_timeout - (time.monotonic() - started_at)
                    if remaining <= 0:
                        self._cancel_future(future, completed)
                        raise TimeoutError("Harbor environment operation timed out")
                    poll_timeout = min(0.1, remaining)
                else:
                    poll_timeout = 0.1
                try:
                    return future.result(timeout=poll_timeout)
                except concurrent.futures.TimeoutError:
                    if future.done():
                        return future.result()
                    continue
        except (HarborBridgeCancelled, TimeoutError):
            self._cancel_future(future, completed)
            raise

    def run(
        self,
        command: str,
        cwd: str | None = None,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
    ) -> HarborCommandResult:
        effective_timeout = self._remaining_timeout(timeout)
        result = self._submit(
            self.environment.exec(
                command,
                cwd=cwd or self.cwd,
                env=env,
                timeout_sec=(
                    max(1, math.ceil(effective_timeout))
                    if effective_timeout is not None
                    else None
                ),
            ),
            effective_timeout,
        )
        return HarborCommandResult(
            stdout=(getattr(result, "stdout", None) or "")[-self.output_limit :],
            stderr=(getattr(result, "stderr", None) or "")[-self.output_limit :],
            return_code=int(getattr(result, "return_code", -1)),
        )

    shell = run
    exec_bash = run

    def resolve_path(self, path: str) -> str:
        candidate = PurePosixPath(path)
        if candidate.is_absolute():
            return str(candidate)
        if self._resolved_cwd is None:
            with self._cwd_lock:
                if self._resolved_cwd is None:
                    result = self.run("pwd", timeout=30)
                    if result.return_code:
                        raise OSError(result.stderr or result.stdout)
                    self._resolved_cwd = (result.stdout or "/").strip() or "/"
        return str(PurePosixPath(self._resolved_cwd) / candidate)

    def mkdir(self, path: str, parents: bool = True, exist_ok: bool = True) -> None:
        path = self.resolve_path(path)
        flags = "-p " if parents or exist_ok else ""
        result = self.run(f"mkdir {flags}{shlex.quote(path)}", cwd="/")
        if result.return_code:
            raise OSError(result.stderr or result.stdout)

    def write_file(self, path: str, content: str) -> None:
        self.upload(content.encode("utf-8"), path)

    def append_file(self, path: str, content: str) -> None:
        path = self.resolve_path(path)
        payload = base64.b64encode(content.encode("utf-8")).decode("ascii")
        command = (
            f"mkdir -p {shlex.quote(str(Path(path).parent))} && "
            f"printf %s {shlex.quote(payload)} | base64 -d >> {shlex.quote(path)}"
        )
        result = self.run(command, cwd="/")
        if result.return_code:
            raise OSError(result.stderr or result.stdout)

    def read_file(self, path: str) -> str:
        return self.download(path).decode("utf-8", errors="replace")

    def upload(self, data: bytes, path: str) -> None:
        self.check_cancelled()
        path = self.resolve_path(path)
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as handle:
            handle.write(data)
            source = Path(handle.name)
        try:
            parent = str(Path(path).parent)
            self.mkdir(parent)
            self._submit(self.environment.upload_file(source, path))
        finally:
            source.unlink(missing_ok=True)

    def download(self, path: str) -> bytes:
        self.check_cancelled()
        path = self.resolve_path(path)
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            target = Path(handle.name)
        try:
            self._submit(self.environment.download_file(path, target))
            return target.read_bytes()
        finally:
            target.unlink(missing_ok=True)

    def file_exists(self, path: str) -> bool:
        path = self.resolve_path(path)
        return self.run(f"test -e {shlex.quote(path)}", cwd="/", timeout=10).return_code == 0

    exists = file_exists

    def list_files(self, path: str) -> list[str]:
        path = self.resolve_path(path)
        result = self.run(
            f"find {shlex.quote(path)} -mindepth 1 -maxdepth 1 -printf '%f\\n' | sort",
            cwd="/",
            timeout=30,
        )
        if result.return_code:
            raise OSError(result.stderr or result.stdout)
        return [line for line in result.stdout.splitlines() if line]


async def run_sync_with_cancellation(
    callback: Callable[[], T],
    *,
    cancel_event: threading.Event | None = None,
) -> T:
    """Run a synchronous native loop without blocking Harbor's event loop."""

    event = cancel_event or threading.Event()
    task = asyncio.create_task(asyncio.to_thread(callback))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        event.set()
        try:
            await asyncio.shield(task)
        except BaseException:
            pass
        raise


__all__ = [
    "HarborBridgeCancelled",
    "HarborCommandResult",
    "HarborShellBridge",
    "run_sync_with_cancellation",
]
