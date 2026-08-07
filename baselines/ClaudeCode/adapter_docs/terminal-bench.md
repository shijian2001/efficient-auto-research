# Claude Code — Terminal-Bench

Status: **ready through Harbor's built-in `claude-code` Agent**.

```bash
bash BenchmarkAdapters/environments/install.sh terminal-bench-2.claude-code
python -m BenchmarkAdapters terminal --agent claude-code \
  --task fix-git --concurrency 1 --dry-run
```

Authentication follows Harbor's Claude Code adapter. Credentials remain
runtime environment values and are not accepted in sensitive Agent kwargs.
