# Custom Agent Integration

Terminal-Bench uses Harbor `0.20.0` for task provisioning, Docker lifecycle,
Agent timeout enforcement, verification, and result aggregation.

## Adapter Type

Host-side native Python loops subclass Harbor `BaseAgent` and invoke task
commands through `BaseEnvironment`. The shared bridge is
`agent_adapters.shared.harbor_shell.HarborShellBridge`.

Use `BaseInstalledAgent` only when the complete Agent executable should be
installed inside every task image.

## Lifecycle

1. Harbor starts the task environment.
2. Harbor calls `setup(environment)`.
3. Harbor calls `run(instruction, environment, context)` with the task timeout.
4. The Agent returns or fails.
5. Harbor starts the official verifier phase.

The custom Agent must not inspect `/tests`, verifier code, or hidden expected
outputs. No verifier is imported into the Agent process.

## Authentication

Credentials are runtime-only. For OpenAI-compatible Agents set
`OPENAI_API_KEY` and `OPENAI_BASE_URL`; do not place keys in `--agent-kwarg`.
Codex can use Harbor's documented auth-file support when explicitly configured.

## Example

```bash
python -m BenchmarkAdapters terminal --agent ai-scientist \
  --task fix-git --attempts 1 --concurrency 1 --dry-run
```

Current readiness is reported by `python -m BenchmarkAdapters.status` and
documented in `terminal-bench-2/agent_adapters/README.md`.
