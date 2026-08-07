# AiScientist — Terminal-Bench

Status: **ready for Harbor command execution**.

The adapter is `agent_adapters.ai_scientist:AiScientistTerminalAgent`. It
reuses AiScientist's native `aisci_agent_runtime.subagents.base.Subagent.run`
loop and native tool schemas. Only shell/file access is adapted through the
shared Harbor bridge.

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
