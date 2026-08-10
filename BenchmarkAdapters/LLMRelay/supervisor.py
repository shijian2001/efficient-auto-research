"""Host-owned relay process supervisor shared by every benchmark."""

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
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]


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
    upstream_api_key: str | None = None
    upstream_proxy: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    temperature: float | None = None
    model_parameters: Mapping[str, Any] | None = None
    request_timeout_seconds: int | None = None
    retry_policy: Mapping[str, Any] | None = None
    upstream_api: str | None = None
    check_upstream_ready: bool = True
    max_upstream_calls: int | None = None
    process: subprocess.Popen | None = None

    def _resolved_upstream_api(self) -> str:
        parameters = dict(self.model_parameters or {})
        api_mode = str(parameters.get("api_mode", "")).strip().lower()
        upstream_api = (
            self.upstream_api
            or (
                "responses"
                if api_mode == "responses"
                and parameters.get("use_completion_api") is not True
                else "chat"
            )
        )
        if upstream_api not in {"chat", "responses"}:
            raise RuntimeError("relay upstream API must be chat or responses")
        return upstream_api

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
        import httpx

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
        if not self.model:
            raise RuntimeError("relay model must be configured explicitly")
        if self._resolved_upstream_api() == "responses":
            path = "/v1/responses"
            request_body = {
                "model": self.model,
                "max_output_tokens": 8,
                "input": "Reply READY",
            }
        else:
            path = "/v1/chat/completions"
            request_body = {
                "model": self.model,
                "max_tokens": 8,
                "messages": [{"role": "user", "content": "Reply READY"}],
            }
        payload = json.dumps(request_body).encode("utf-8")
        response = self._request(path, payload=payload, timeout=120)
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
        import httpx

        if not self.model:
            raise RuntimeError("relay model must be configured explicitly")
        if not self.upstream_base_url:
            raise RuntimeError("relay upstream base URL must be configured explicitly")
        if not self.model_parameters:
            raise RuntimeError("relay model parameters must be configured explicitly")
        if self.request_timeout_seconds is not None and self.request_timeout_seconds < 1:
            raise RuntimeError("relay request timeout must be positive")
        if self.max_upstream_calls is not None and self.max_upstream_calls < 1:
            raise RuntimeError("relay max upstream calls must be positive")
        upstream_key = (
            self.upstream_api_key
            or os.environ.get("UPSTREAM_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
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
        allowed_environment = {
            "LANG",
            "LC_ALL",
            "PATH",
            "SSL_CERT_DIR",
            "SSL_CERT_FILE",
        }
        environment = {
            key: value for key, value in os.environ.items() if key in allowed_environment
        }
        environment.update(
            {
                "UPSTREAM_BASE_URL": self.upstream_base_url,
                "UPSTREAM_API_KEY": upstream_key,
                "LLM_FORCE_MODEL": self.model,
                "LLM_UPSTREAM_PROXY": self.upstream_proxy or environment.get(
                    "LLM_UPSTREAM_PROXY",
                    environment.get("CLASH_PROXY", ""),
                ),
                "LLM_TOKEN_LOG_PATH": str(self.token_log_path),
                "LLM_PROXY_AGENT_NAME": self.agent,
                "LLM_PROXY_API_KEY": "proxy",
            }
        )
        parameters = dict(self.model_parameters or {})
        upstream_api = self._resolved_upstream_api()
        if self.reasoning_effort is not None:
            parameters.setdefault("reasoning_effort", self.reasoning_effort)
        if self.temperature is not None:
            parameters.setdefault("temperature", self.temperature)
        environment["LLM_FORCE_PARAMETERS_JSON"] = json.dumps(parameters, sort_keys=True)
        environment["LLM_UPSTREAM_API"] = upstream_api
        environment["LLM_UPSTREAM_TIMEOUT"] = (
            "" if self.request_timeout_seconds is None else str(self.request_timeout_seconds)
        )
        retry_policy = dict(self.retry_policy or {})
        retries = retry_policy.get("max_retries")
        if retries is None and retry_policy.get("max_attempts") is not None:
            retries = int(retry_policy["max_attempts"]) - 1
        if retries is not None and int(retries) < 0:
            raise RuntimeError("relay max retries must be non-negative")
        environment["LLM_MAX_RETRIES"] = "0" if retries is None else str(int(retries))
        environment["LLM_MAX_UPSTREAM_CALLS"] = (
            "" if self.max_upstream_calls is None else str(self.max_upstream_calls)
        )
        if "reasoning_effort" in parameters:
            environment["LLM_REASONING_EFFORT"] = str(parameters["reasoning_effort"])
        if "temperature" in parameters:
            environment["LLM_TEMPERATURE"] = str(parameters["temperature"])
        if self.token_log_path.exists() or self.token_log_path.is_symlink():
            raise RuntimeError(f"refusing to reuse relay telemetry path: {self.token_log_path}")
        try:
            log_handle = self.log_path.open("x", encoding="utf-8")
        except FileExistsError as exc:
            raise RuntimeError(f"refusing to overwrite relay log: {self.log_path}") from exc
        try:
            command = [sys.executable, "-u", str(Path(__file__).with_name("server.py"))]
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
            if self.check_upstream_ready:
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
