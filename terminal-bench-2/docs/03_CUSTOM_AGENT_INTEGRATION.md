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

Example:

```bash
TB2_AGENT='agent_adapters.my_agent:MyAgent' \
TB2_MODEL='provider/model-name' \
TB2_INCLUDE_TASK='openssl-selfsigned-cert' \
./scripts/run_custom_agent.sh
```
