# Harbor Agent Adapters

This package contains Harbor `0.20.0` `BaseAgent` integrations for the local
Terminal-Bench task dataset.

## Shared Layer

`shared/harbor_shell.py` provides one synchronous facade over Harbor's
asynchronous `BaseEnvironment` API. Native Agent loops reuse it for command
execution and file transfer. It centralizes:

- Harbor event-loop bridging;
- cancellation and deadline checks;
- command output limits;
- container WORKDIR discovery for relative files;
- upload/download cleanup.

It does not contain an LLM loop or pretend to implement an Agent's search
algorithm.

## Registered Agents

| Import path | Native loop | State |
|---|---|---|
| Harbor built-in `codex` | Codex CLI | legacy direct smoke |
| Harbor built-in `claude-code` | Claude Code | legacy direct smoke |
| `agent_adapters.arbor:ArborTerminalAgent` | `arbor.core.agent.Agent.run` | legacy direct smoke |
| `agent_adapters.ai_scientist:AiScientistTerminalAgent` | AiScientist `Subagent.run` | legacy direct smoke |
| `agent_adapters.ear:EARTerminalAgent` | EAR graph search | legacy direct path disabled |
| `agent_adapters.mlevolve:MLEvolveTerminalAgent` | MLEvolve search | legacy direct path disabled |
| `agent_adapters.ml_master_2:MLMaster2Agent` | ML-Master 2 workflow | legacy direct path disabled |

These three Harbor registrations are retained as explicit non-comparable direct
path guards. The current Terminal AO comparison uses the native repository
backends under `BenchmarkAdapters/TerminalAO/`; importability alone is not
formal readiness, and the shared AO protocol, model track, clean source and
real smoke are still required.

## Launch

Use the canonical command builder from the repository root:

```bash
python -m BenchmarkAdapters terminal --agent arbor \
  --task fix-git --concurrency 1 --dry-run
```

Or launch an import path directly with the generic script:

```bash
TB2_AGENT='agent_adapters.arbor:ArborTerminalAgent' \
TB2_MODEL='gpt-5.5' \
TB2_INCLUDE_TASK='fix-git' \
./scripts/run_custom_agent.sh
```

Harbor runs the official verifier only after the Agent returns. The adapters do
not access `/tests`, verifier source, or a project-owned scoring function.
