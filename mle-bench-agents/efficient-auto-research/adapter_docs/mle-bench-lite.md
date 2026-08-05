# EAR — MLE-Bench Lite

Use the shared project adapter at `BenchmarkAdapters/MLEBenchLite/adapter.py` with
`--agent ear`. It delegates to the existing `docker-eval/run_in_docker.sh`
launcher, preserving EAR's native search, public-data isolation, token logs,
and official external grader contract.

Preview:

```bash
python -m benchmark_adapters mle --agent ear \
  --competition-id spooky-author-identification \
  --data-root /path/to/mle-bench-data --output-dir /path/to/run --dry-run
```
