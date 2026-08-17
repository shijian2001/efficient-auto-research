# EAR — MLE-Bench Lite

The launcher is implemented, but this is not evidence of a formal score. The
shared schema-v2 manifest, model track, clean source and real scored smoke must
be ready before this cell enters the seven-Agent comparison.

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
