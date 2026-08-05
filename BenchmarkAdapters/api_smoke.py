"""Native-agent API smoke command."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .registry import AGENTS, ROOT
from .relay import RelayProcess


PYTHON_CLIENTS = {
    "ear": ROOT / "mle-bench-lite/.venv/bin/python",
    "mlevolve": ROOT / "mle-bench-lite/.venv/bin/python",
    "arbor": ROOT / "baselines/Arbor/.venv/bin/python",
    "ml-master-2": ROOT / "baselines/EvoMaster/.venv/bin/python",
    "ai-scientist": ROOT / "baselines/AiScientist/.venv/bin/python",
}

OPENAI_SMOKE = """
from openai import OpenAI
import os
client = OpenAI(api_key='proxy', base_url=os.environ['OPENAI_BASE_URL'], max_retries=0)
response = client.chat.completions.create(
    model='gpt-5.5',
    messages=[{'role': 'user', 'content': 'Reply with exactly API_READY'}],
)
text = response.choices[0].message.content or ''
print('API_READY' if 'API_READY' in text else text)
"""


def _python_smoke(agent: str, run_dir: Path) -> dict[str, object]:
    executable = PYTHON_CLIENTS[agent]
    with RelayProcess(
        agent=agent,
        log_path=run_dir / "relay.log",
        token_log_path=run_dir / "token_usage.jsonl",
    ) as relay:
        environment = os.environ.copy()
        environment.update(
            {
                "OPENAI_API_KEY": "proxy",
                "OPENAI_BASE_URL": relay.base_url,
                "NO_PROXY": "localhost,127.0.0.1",
                "no_proxy": "localhost,127.0.0.1",
            }
        )
        result = subprocess.run(
            [str(executable), "-c", OPENAI_SMOKE],
            capture_output=True,
            text=True,
            env=environment,
            timeout=600,
        )
    return {
        "command": [str(executable), "-c", "<redacted-smoke-program>"],
        "exit_code": result.returncode,
        "ready": "API_READY" in result.stdout,
        "stdout": result.stdout.strip()[-1000:],
        "stderr": result.stderr.strip()[-2000:],
    }


def _codex_smoke(run_dir: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="codex-smoke-") as temporary:
        codex_home = Path(temporary)
        auth_path = codex_home / "auth.json"
        auth_path.write_text('{"OPENAI_API_KEY":"proxy"}\n', encoding="utf-8")
        auth_path.chmod(0o600)
        with RelayProcess(
            agent="codex",
            log_path=run_dir / "relay.log",
            token_log_path=run_dir / "token_usage.jsonl",
        ) as relay:
            environment = os.environ.copy()
            environment.update(
                {
                    "CODEX_HOME": str(codex_home),
                    "OPENAI_API_KEY": "proxy",
                    "OPENAI_BASE_URL": relay.base_url,
                    "NO_PROXY": "localhost,127.0.0.1",
                    "no_proxy": "localhost,127.0.0.1",
                }
            )
            command = [
                "codex",
                "exec",
                "--ephemeral",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--model",
                "gpt-5.5",
                "Reply with exactly API_READY",
            ]
            result = subprocess.run(
                command,
                cwd=run_dir,
                capture_output=True,
                text=True,
                env=environment,
                timeout=600,
            )
    return {
        "command": command,
        "exit_code": result.returncode,
        "ready": "API_READY" in result.stdout,
        "stdout": result.stdout.strip()[-2000:],
        "stderr": result.stderr.strip()[-2000:],
    }


def _claude_smoke(run_dir: Path) -> dict[str, object]:
    upstream_key = os.environ.get("UPSTREAM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not upstream_key:
        raise RuntimeError("set UPSTREAM_API_KEY or OPENAI_API_KEY")
    environment = os.environ.copy()
    environment.update(
        {
            "ANTHROPIC_API_KEY": upstream_key,
            "ANTHROPIC_BASE_URL": environment.get(
                "ANTHROPIC_BASE_URL", "https://relay.shuai-ederson-clow.xyz"
            ),
            "HTTP_PROXY": environment.get("CLASH_PROXY", "http://127.0.0.1:17892"),
            "HTTPS_PROXY": environment.get("CLASH_PROXY", "http://127.0.0.1:17892"),
        }
    )
    command = [
        "claude",
        "--print",
        "--bare",
        "--no-session-persistence",
        "--model",
        "gpt-5.5",
        "--tools",
        "",
        "--max-turns",
        "1",
        "Reply with exactly API_READY",
    ]
    result = subprocess.run(
        command,
        cwd=run_dir,
        capture_output=True,
        text=True,
        env=environment,
        timeout=600,
    )
    return {
        "command": command,
        "exit_code": result.returncode,
        "ready": "API_READY" in result.stdout,
        "stdout": result.stdout.strip()[-2000:],
        "stderr": result.stderr.strip()[-2000:],
    }


def run_smoke(agent: str, output_root: Path) -> dict[str, object]:
    run_dir = output_root / agent
    run_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    try:
        if agent in PYTHON_CLIENTS:
            details = _python_smoke(agent, run_dir)
        elif agent == "codex":
            details = _codex_smoke(run_dir)
        elif agent == "claude-code":
            details = _claude_smoke(run_dir)
        else:
            raise ValueError(f"unknown agent: {agent}")
        status = "passed" if details["ready"] and details["exit_code"] == 0 else "failed"
    except Exception as exc:
        details = {"error": f"{type(exc).__name__}: {exc}"}
        status = "failed"
    record = {
        "agent": agent,
        "display_name": AGENTS[agent].display_name,
        "status": status,
        "duration_seconds": round(time.time() - started, 3),
        "model": "gpt-5.5",
        "credential_persisted": False,
        "details": details,
    }
    (run_dir / "result.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=["all", *AGENTS], default="all")
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    output_root = args.output_root or ROOT / "run-logs/api-smoke"
    agents = list(AGENTS) if args.agent == "all" else [args.agent]
    records = [run_smoke(agent, output_root) for agent in agents]
    print(json.dumps(records, indent=2, ensure_ascii=False))
    raise SystemExit(0 if all(record["status"] == "passed" for record in records) else 1)


if __name__ == "__main__":
    main()
