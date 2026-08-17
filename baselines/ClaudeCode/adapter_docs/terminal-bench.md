# Claude Code — Terminal-Bench

Status: **legacy direct-solving smoke only; not a Terminal AO score**.

```bash
bash BenchmarkAdapters/environments/install.sh terminal-bench-2.claude-code
python -m BenchmarkAdapters terminal --agent claude-code \
  --task fix-git --concurrency 1 --dry-run
```

Authentication follows Harbor's Claude Code adapter. Credentials remain
runtime environment values and are not accepted in sensitive Agent kwargs.
This direct path is for infrastructure checks; the formal comparison uses the
36-dev/53-held-out Terminal AO adapter and still needs the shared protocol,
model configuration, clean source and real smoke.
