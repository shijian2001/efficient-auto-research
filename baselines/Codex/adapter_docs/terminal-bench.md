# Codex CLI — Terminal-Bench

Status: **legacy direct-solving smoke only; not a Terminal AO score**.

```bash
bash BenchmarkAdapters/environments/install.sh terminal-bench-2.codex
python -m BenchmarkAdapters terminal --agent codex \
  --task fix-git --concurrency 1 --dry-run
```

Harbor owns the task container and verifier lifecycle. Codex authentication is
runtime-only and no key is written into adapter files. This direct path is for
infrastructure checks; the formal comparison uses the 36-dev/53-held-out
Terminal AO adapter and still needs the shared protocol, model configuration,
clean source and real smoke.
