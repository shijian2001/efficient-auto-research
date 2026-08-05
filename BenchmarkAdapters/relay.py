"""Local relay process supervisor shared by benchmark adapters."""

from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path


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
    process: subprocess.Popen | None = None

    @property
    def base_url(self) -> str:
        if self.port is None:
            raise RuntimeError("relay has not started")
        return f"http://127.0.0.1:{self.port}/v1"

    def __enter__(self) -> "RelayProcess":
        upstream_key = os.environ.get("UPSTREAM_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not upstream_key:
            raise RuntimeError("set UPSTREAM_API_KEY or OPENAI_API_KEY")
        self.port = self.port or _free_port()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_log_path.parent.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment.update(
            {
                "UPSTREAM_BASE_URL": environment.get(
                    "UPSTREAM_BASE_URL", "https://relay.shuai-ederson-clow.xyz/v1"
                ),
                "UPSTREAM_API_KEY": upstream_key,
                "LLM_FORCE_MODEL": environment.get("MODEL", "gpt-5.5"),
                "LLM_REASONING_EFFORT": environment.get("LLM_REASONING_EFFORT", "high"),
                "LLM_UPSTREAM_PROXY": environment.get(
                    "LLM_UPSTREAM_PROXY",
                    environment.get("CLASH_PROXY", "http://127.0.0.1:17892"),
                ),
                "LLM_TOKEN_LOG_PATH": str(self.token_log_path),
                "LLM_PROXY_AGENT_NAME": self.agent,
            }
        )
        log_handle = self.log_path.open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [
                str(ROOT / "mle-bench-lite/.venv/bin/python"),
                "-u",
                str(ROOT / "docker-eval/llm_relay_proxy.py"),
                "--port",
                str(self.port),
            ],
            env=environment,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
        log_handle.close()
        for _ in range(100):
            if self.process.poll() is not None:
                raise RuntimeError(f"relay exited early; see {self.log_path}")
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/health", timeout=1
                ).read()
                return self
            except OSError:
                time.sleep(0.1)
        raise RuntimeError(f"relay did not become ready; see {self.log_path}")

    def __exit__(self, *_args) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


__all__ = ["RelayProcess", "ROOT"]
