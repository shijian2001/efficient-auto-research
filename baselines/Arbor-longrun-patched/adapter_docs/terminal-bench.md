# Arbor — Terminal-Bench

Status: **legacy direct-solving smoke only; not a Terminal AO score**.

The adapter is `agent_adapters.arbor:ArborTerminalAgent`. It reuses Arbor's
native `arbor.core.agent.Agent.run` ReAct loop and OpenAI-compatible provider.
The Agent receives Harbor-backed subclasses of Arbor's native Bash, Read,
Write, and Edit tools, preserving their published schemas and edit semantics;
host git/worktree tools are deliberately excluded.

This 89-task direct path is retained for infrastructure checks. The formal
comparison uses the 36-dev/53-held-out Terminal AO adapter; the shared protocol,
model configuration, clean source and real smoke are still required before
formal scoring.

```bash
bash BenchmarkAdapters/environments/install.sh terminal-bench-2.arbor
python -m BenchmarkAdapters terminal --agent arbor \
  --task fix-git --concurrency 1 --dry-run
```

The locked combined runtime is
`BenchmarkAdapters/environments/terminal/arbor/`.
