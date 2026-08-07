# Arbor — Terminal-Bench

Status: **ready for Harbor command execution**.

The adapter is `agent_adapters.arbor:ArborTerminalAgent`. It reuses Arbor's
native `arbor.core.agent.Agent.run` ReAct loop and OpenAI-compatible provider.
The Agent receives Harbor-backed subclasses of Arbor's native Bash, Read,
Write, and Edit tools, preserving their published schemas and edit semantics;
host git/worktree tools are deliberately excluded.

```bash
bash BenchmarkAdapters/environments/install.sh terminal-bench-2.arbor
python -m BenchmarkAdapters terminal --agent arbor \
  --task fix-git --concurrency 1 --dry-run
```

The locked combined runtime is
`BenchmarkAdapters/environments/terminal/arbor/`.
