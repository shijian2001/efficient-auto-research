# Custom Agent Integration

Terminal-Bench evaluates this repository's own agent. Harbor is used only for
task provisioning, Docker lifecycle management, timeout enforcement, trajectory
collection, verification, and score aggregation.

## Recommended Adapter Type

Use an external Harbor `BaseAgent` adapter when the research agent runs outside
the task container and invokes commands through Harbor's environment interface.

Use `BaseInstalledAgent` only when the complete agent executable should be
installed and executed inside each task container.

## Authentication

The host already contains `~/.codex/auth.json`. Harbor's Codex adapter defaults
to `OPENAI_API_KEY`; to explicitly reuse the local Codex login file, set:

```bash
export CODEX_FORCE_AUTH_JSON=1
```

The adapter package is installed at `agent_adapters/`, and the generic launcher
is `scripts/run_custom_agent.sh`. Add the production adapter after selecting the
Agent's entry point, command execution interface, and authentication contract.

The project-specific Harness-Engineering AO protocol uses
`../BenchmarkAdapters/TerminalBench/adapter.py`. EAR, MLEvolve, ML-Master 2.0,
and AiScientist share its isolated repository backend with thin strategy
profiles; Arbor, Codex, and Claude Code use repository-native CLIs. This AO path
is separate from the per-task Harbor `BaseAgent` interface described here.

AO split evaluation uses a disposable, no-network workspace. The evaluator is
trusted benchmark code: held-out labels that must remain confidential cannot be
passed directly into imported candidate code and should stay behind a separate
trusted scoring process.

Example:

```bash
TB2_AGENT='agent_adapters.my_agent:MyAgent' \
TB2_MODEL='provider/model-name' \
TB2_INCLUDE_TASK='openssl-selfsigned-cert' \
./scripts/run_custom_agent.sh
```
