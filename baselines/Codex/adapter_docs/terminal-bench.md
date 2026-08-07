# Codex CLI — Terminal-Bench

Status: **ready through Harbor's built-in `codex` Agent**.

```bash
bash BenchmarkAdapters/environments/install.sh terminal-bench-2.codex
python -m BenchmarkAdapters terminal --agent codex \
  --task fix-git --concurrency 1 --dry-run
```

Harbor owns the task container and verifier lifecycle. Codex authentication is
runtime-only and no key is written into adapter files.
