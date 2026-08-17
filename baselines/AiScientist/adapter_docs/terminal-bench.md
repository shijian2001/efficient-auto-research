# AiScientist — Terminal-Bench

Status: **legacy direct-solving smoke only; not a Terminal AO score**.

The adapter is `agent_adapters.ai_scientist:AiScientistTerminalAgent`. It
reuses AiScientist's native `aisci_agent_runtime.subagents.base.Subagent.run`
loop and native tool schemas. Only shell/file access is adapted through the
shared Harbor bridge.

This 89-task direct path is retained for infrastructure checks. The formal
comparison uses the 36-dev/53-held-out Terminal AO adapter and its explicit
`ai-scientist-terminal-variant`; the shared protocol, model configuration,
clean source and real smoke are still required before formal scoring.

Install and preview:

```bash
bash BenchmarkAdapters/environments/install.sh terminal-bench-2.ai-scientist
python -m BenchmarkAdapters terminal --agent ai-scientist \
  --task fix-git --concurrency 1 --dry-run
```

The locked combined runtime is
`BenchmarkAdapters/environments/terminal/ai-scientist/`. A non-completed native
status is recorded by the Agent without suppressing Harbor's official verifier,
so partial workspace changes are still evaluated. Harbor cancellation continues
to cancel the native loop and its in-flight tools.
