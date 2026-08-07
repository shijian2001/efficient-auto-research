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
| Harbor built-in `codex` | Codex CLI | ready |
| Harbor built-in `claude-code` | Claude Code | ready |
| `agent_adapters.arbor:ArborTerminalAgent` | `arbor.core.agent.Agent.run` | ready |
| `agent_adapters.ai_scientist:AiScientistTerminalAgent` | AiScientist `Subagent.run` | ready |
| `agent_adapters.ear:EARTerminalAgent` | EAR graph search | fail-closed |
| `agent_adapters.mlevolve:MLEvolveTerminalAgent` | MLEvolve search | fail-closed |
| `agent_adapters.ml_master_2:MLMaster2Agent` | ML-Master 2 workflow | fail-closed |

The three search/workflow adapters remain blocked until their candidate or
workspace semantics are correct. Importability is not treated as readiness.

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
