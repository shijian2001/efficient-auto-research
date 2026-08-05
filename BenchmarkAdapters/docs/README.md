# Shared Baseline Adapters

`BenchmarkAdapters/` is the canonical location for project-owned benchmark
adapters. Benchmark implementations are separated by benchmark:

```text
BenchmarkAdapters/
├── MLEBenchLite/adapter.py
├── TerminalBench/adapter.py
├── contracts.py
├── process.py
├── registry.py
├── agents.py
├── cli.py
├── api_smoke.py
├── relay.py
└── status.py
```

Use `BenchmarkAdapters.MLEBenchLite.adapter` for direct MLE-Bench Lite runs and
`BenchmarkAdapters.TerminalBench.adapter` for the modified Terminal-Bench
Harness-Engineering AO protocol. Shared process, relay, environment, and error
handling stays in the root modules.

```bash
python -m BenchmarkAdapters mle --agent arbor \
  --competition-id spooky-author-identification \
  --data-root /path/to/mle-bench-data \
  --output-dir /path/to/run --dry-run

python -m BenchmarkAdapters terminal --agent codex \
  --harness-dir /path/to/terminus-2 \
  --eval-script /path/to/run_eval.py \
  --dev-data /path/to/data/dev.json \
  --test-data /path/to/data/test.json \
  --output-dir /path/to/ao-run --split dev --dry-run
```

Defaults are model `gpt-5.5`, relay
`https://relay.shuai-ederson-clow.xyz/v1`, and proxy
`http://127.0.0.1:17892`. Credentials are process-only and are not written to
manifests or documentation.

The lowercase `benchmark_adapters` package remains as a compatibility wrapper
for existing imports and commands.

Agent-specific notes remain next to each installed Agent under `adapter_docs/`:

```text
baselines/<Agent>/adapter_docs/mle-bench-lite.md
baselines/<Agent>/adapter_docs/terminal-bench-ao.md
```
