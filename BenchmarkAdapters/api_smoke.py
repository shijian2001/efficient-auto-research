"""Minimal endpoint smoke command using each Agent runtime."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from .contracts import protect_generated_output
from .registry import AGENTS, ROOT
from .relay import RelayProcess


PYTHON_CLIENTS = {
    "ear": ROOT / "mle-bench-agents/efficient-auto-research/.venv/bin/python",
    "mlevolve": ROOT / "BenchmarkAdapters/environments/agents/mlevolve/.venv/bin/python",
    "arbor": ROOT / "BenchmarkAdapters/environments/terminal/arbor/.venv/bin/python",
    "ml-master-2": ROOT / "baselines/EvoMaster/.venv/bin/python",
    "ai-scientist": ROOT / "BenchmarkAdapters/environments/terminal/ai-scientist/.venv/bin/python",
}

OPENAI_SMOKE = """
from openai import OpenAI
import os
client = OpenAI(api_key='proxy', base_url=os.environ['OPENAI_BASE_URL'], max_retries=0)
response = client.chat.completions.create(
    model=os.environ['SMOKE_MODEL_ID'],
    messages=[{'role': 'user', 'content': 'Reply with exactly API_READY'}],
)
text = response.choices[0].message.content or ''
print('API_READY' if 'API_READY' in text else text)
"""


def _relay(
    *, agent: str, run_dir: Path, model: str, upstream_base_url: str, model_parameters: dict[str, object]
) -> RelayProcess:
    return RelayProcess(
        agent=agent,
        log_path=run_dir / "relay.log",
        token_log_path=run_dir / "token_usage.jsonl",
        upstream_base_url=upstream_base_url,
        model=model,
        model_parameters=model_parameters,
    )


def _python_smoke(
    agent: str,
    run_dir: Path,
    *,
    model: str,
    upstream_base_url: str,
    model_parameters: dict[str, object],
) -> dict[str, object]:
    executable = PYTHON_CLIENTS[agent]
    if not executable.is_file():
        raise FileNotFoundError(f"Agent runtime is not installed: {executable}")
    with _relay(
        agent=agent,
        run_dir=run_dir,
        model=model,
        upstream_base_url=upstream_base_url,
        model_parameters=model_parameters,
    ) as relay:
        environment = os.environ.copy()
        environment.update(
            {
                "OPENAI_API_KEY": "proxy",
                "OPENAI_BASE_URL": relay.base_url,
                "SMOKE_MODEL_ID": model,
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


def _codex_smoke(
    run_dir: Path,
    *,
    model: str,
    upstream_base_url: str,
    model_parameters: dict[str, object],
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="codex-smoke-") as temporary:
        codex_home = Path(temporary)
        auth_path = codex_home / "auth.json"
        auth_path.write_text('{"OPENAI_API_KEY":"proxy"}\n', encoding="utf-8")
        auth_path.chmod(0o600)
        with _relay(
            agent="codex",
            run_dir=run_dir,
            model=model,
            upstream_base_url=upstream_base_url,
            model_parameters=model_parameters,
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
                model,
                "-c",
                'model_provider="flm_relay"',
                "-c",
                'model_providers.flm_relay.name="FLM relay"',
                "-c",
                f'model_providers.flm_relay.base_url="{relay.base_url}"',
                "-c",
                'model_providers.flm_relay.wire_api="responses"',
                "-c",
                "model_providers.flm_relay.requires_openai_auth=true",
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


def _claude_smoke(
    run_dir: Path,
    *,
    model: str,
    upstream_base_url: str,
    model_parameters: dict[str, object],
) -> dict[str, object]:
    with _relay(
        agent="claude-code",
        run_dir=run_dir,
        model=model,
        upstream_base_url=upstream_base_url,
        model_parameters=model_parameters,
    ) as relay:
        environment = os.environ.copy()
        environment.update(
            {
                "ANTHROPIC_API_KEY": "proxy",
                "ANTHROPIC_BASE_URL": relay.base_url.removesuffix("/v1"),
                "NO_PROXY": "localhost,127.0.0.1",
                "no_proxy": "localhost,127.0.0.1",
            }
        )
        command = [
            "claude",
            "--print",
            "--bare",
            "--no-session-persistence",
            "--model",
            model,
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


def run_smoke(
    agent: str,
    output_root: Path,
    *,
    model: str,
    upstream_base_url: str,
    model_parameters: dict[str, object],
) -> dict[str, object]:
    output_root = protect_generated_output(output_root, ROOT)
    run_dir = output_root / agent
    run_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    try:
        if agent in PYTHON_CLIENTS:
            details = _python_smoke(
                agent,
                run_dir,
                model=model,
                upstream_base_url=upstream_base_url,
                model_parameters=model_parameters,
            )
        elif agent == "codex":
            details = _codex_smoke(
                run_dir,
                model=model,
                upstream_base_url=upstream_base_url,
                model_parameters=model_parameters,
            )
        elif agent == "claude-code":
            details = _claude_smoke(
                run_dir,
                model=model,
                upstream_base_url=upstream_base_url,
                model_parameters=model_parameters,
            )
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
        "model": model,
        "smoke_kind": "endpoint-only",
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
    parser.add_argument("--model", required=True)
    parser.add_argument("--upstream-base-url", required=True)
    parser.add_argument("--model-parameters-json", required=True)
    args = parser.parse_args()
    output_root = args.output_root or ROOT / "run-logs/api-smoke"
    agents = list(AGENTS) if args.agent == "all" else [args.agent]
    model_parameters = json.loads(args.model_parameters_json)
    if not isinstance(model_parameters, dict) or not model_parameters:
        raise SystemExit("--model-parameters-json must be a non-empty JSON object")
    records = [
        run_smoke(
            agent,
            output_root,
            model=args.model,
            upstream_base_url=args.upstream_base_url,
            model_parameters=model_parameters,
        )
        for agent in agents
    ]
    print(json.dumps(records, indent=2, ensure_ascii=False))
    raise SystemExit(0 if all(record["status"] == "passed" for record in records) else 1)


if __name__ == "__main__":
    main()
