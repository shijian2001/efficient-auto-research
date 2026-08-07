"""Local relay process supervisor shared by benchmark adapters."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class RelayProcess:
    agent: str
    log_path: Path
    token_log_path: Path
    port: int | None = None
    unix_socket: Path | None = None
    upstream_base_url: str | None = None
    upstream_proxy: str | None = None
    model: str = "gpt-5.5"
    reasoning_effort: str = "high"
    temperature: float = 1.0
    process: subprocess.Popen | None = None

    def _stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.unix_socket is not None:
            self.unix_socket.unlink(missing_ok=True)

    def _request(self, path: str, *, payload: bytes | None = None, timeout: float = 120):
        if self.unix_socket is not None:
            transport = httpx.HTTPTransport(uds=str(self.unix_socket))
            with httpx.Client(transport=transport, timeout=timeout) as client:
                if payload is None:
                    return client.get(f"http://relay{path}")
                return client.post(
                    f"http://relay{path}",
                    content=payload,
                    headers={
                        "Authorization": "Bearer proxy",
                        "Content-Type": "application/json",
                    },
                )
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=payload,
            headers=(
                {
                    "Authorization": "Bearer proxy",
                    "Content-Type": "application/json",
                }
                if payload is not None
                else {}
            ),
            method="POST" if payload is not None else "GET",
        )
        return urllib.request.urlopen(request, timeout=timeout)

    def _check_upstream_ready(self) -> None:
        payload = json.dumps(
            {
                "model": self.model,
                "max_tokens": 8,
                "messages": [{"role": "user", "content": "Reply READY"}],
            }
        ).encode("utf-8")
        response = self._request("/v1/chat/completions", payload=payload, timeout=120)
        try:
            status = getattr(response, "status_code", getattr(response, "status", None))
            if status != 200:
                raise RuntimeError(f"relay upstream readiness returned {status}")
        finally:
            response.close()

    @property
    def base_url(self) -> str:
        if self.unix_socket is not None:
            raise RuntimeError("Unix-socket relay has no directly usable HTTP base URL")
        if self.port is None:
            raise RuntimeError("relay has not started")
        return f"http://127.0.0.1:{self.port}/v1"

    def __enter__(self) -> "RelayProcess":
        upstream_key = os.environ.get("UPSTREAM_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not upstream_key:
            raise RuntimeError("set UPSTREAM_API_KEY or OPENAI_API_KEY")
        if self.unix_socket is None:
            self.port = self.port or _free_port()
        else:
            self.unix_socket = self.unix_socket.resolve()
            self.unix_socket.parent.mkdir(parents=True, exist_ok=True)
            self.unix_socket.unlink(missing_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_log_path.parent.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment.update(
            {
                "UPSTREAM_BASE_URL": self.upstream_base_url
                or environment.get("UPSTREAM_BASE_URL", "https://relay.shuai-ederson-clow.xyz/v1"),
                "UPSTREAM_API_KEY": upstream_key,
                "LLM_FORCE_MODEL": self.model,
                "LLM_REASONING_EFFORT": self.reasoning_effort,
                "LLM_TEMPERATURE": str(self.temperature),
                "LLM_UPSTREAM_PROXY": self.upstream_proxy or environment.get(
                    "LLM_UPSTREAM_PROXY",
                    environment.get("CLASH_PROXY", "http://127.0.0.1:17892"),
                ),
                "LLM_TOKEN_LOG_PATH": str(self.token_log_path),
                "LLM_PROXY_AGENT_NAME": self.agent,
                "LLM_PROXY_API_KEY": "proxy",
            }
        )
        log_handle = self.log_path.open("w", encoding="utf-8")
        try:
            command = [
                    sys.executable,
                    "-u",
                    str(ROOT / "docker-eval/llm_relay_proxy.py"),
                ]
            if self.unix_socket is None:
                command.extend(["--port", str(self.port)])
            else:
                command.extend(["--unix-socket", str(self.unix_socket)])
            self.process = subprocess.Popen(
                command,
                env=environment,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )
            for _ in range(100):
                if self.process.poll() is not None:
                    raise RuntimeError(f"relay exited early; see {self.log_path}")
                try:
                    response = self._request("/health", timeout=1)
                    status = getattr(
                        response,
                        "status_code",
                        getattr(response, "status", None),
                    )
                    if status != 200:
                        raise OSError("relay health check was not successful")
                    close = getattr(response, "close", None)
                    if callable(close):
                        close()
                    break
                except (OSError, httpx.HTTPError):
                    time.sleep(0.1)
            else:
                raise RuntimeError(f"relay did not become ready; see {self.log_path}")
            self._check_upstream_ready()
            return self
        except Exception:
            self._stop()
            raise
        finally:
            log_handle.close()

    def __exit__(self, *_args) -> None:
        self._stop()


__all__ = ["RelayProcess", "ROOT"]
