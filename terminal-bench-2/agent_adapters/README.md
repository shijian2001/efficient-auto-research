# Custom Agent Adapters

Harbor is the benchmark runner and isolation layer. The evaluated system remains
your own Agent. Put its Harbor adapter in this Python package and launch it with
the `module.path:ClassName` import path.

## Interface

A custom adapter subclasses Harbor's `BaseAgent` and implements:

- `name()`
- `version()`
- `setup(environment)`
- `run(instruction, environment, context)`

If the Agent is installed and executed inside each task container, use Harbor's
installed-agent patterns as a reference. If it controls the environment from the
host, subclass `BaseAgent` directly.

## Launch

```bash
TB2_AGENT='agent_adapters.my_agent:MyAgent' \
TB2_MODEL='provider/model-name' \
TB2_INCLUDE_TASK='openssl-selfsigned-cert' \
../scripts/run_custom_agent.sh
```

No production adapter is created here because the Agent's actual Python class,
CLI entrypoint, authentication method, and trajectory format have not yet been
specified. The launcher and import package are ready for that code.

## Modified Terminal-Bench AO

The repository's modified Terminal-Bench protocol is the Arbor-style
Harness-Engineering Autonomous Optimization task. Its outer Agent edits a
`terminus-2` repository and the fixed Harbor evaluator scores the resulting
harness. It is not the same contract as a direct Harbor `BaseAgent` that solves
one task inside a container.

The shared AO implementation lives in
`../../BenchmarkAdapters/TerminalBench/adapter.py`. Keep this package for direct Harbor
`BaseAgent` integrations; use the shared adapter for the modified AO protocol
so evaluator, proxy, output, and retry behavior remain common across Agents.

EAR, MLEvolve, ML-Master 2.0, and AiScientist share the isolated runtime under
`../../BenchmarkAdapters/RepositoryAgent/`; Arbor, Codex, and Claude Code keep
their repository-native commands. Optimization uses dev only, and held-out test
evaluation is a separate sandboxed operation.

The evaluator remains part of the trusted benchmark boundary. If it imports the
candidate Harness in-process, it must not hand raw held-out labels to candidate
code; use a separate trusted scoring process for confidential labels.
