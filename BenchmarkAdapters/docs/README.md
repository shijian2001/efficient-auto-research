# Shared Baseline Adapters

The canonical implementation and capability matrix are documented in
`BenchmarkAdapters/README.md`.

The implemented repair checklist, scoring protocol, remaining real-run gates,
and evidence requirements for all seven Agents on MLE-Bench Lite and
Terminal-Bench 36/53 AO are defined in
`BenchmarkAdapters/docs/SEVEN_AGENT_BENCHMARK_REPAIR_PLAN.md`.

The important boundary is benchmark-specific:

- `BenchmarkAdapters/MLEBenchLite/adapter.py` builds MLE-Bench Lite workspaces
  and native launch commands.
- `BenchmarkAdapters/TerminalAO/supervisor.py` owns 36-dev search, revision
  selection, final freeze, and one-shot 53-test evaluation.
- `BenchmarkAdapters/TerminalBench/adapter.py` is a direct-solving smoke path
  and is not comparable with Terminal AO.
- `terminal-bench-2/agent_adapters/shared/harbor_shell.py` is the common
  synchronous Harbor environment bridge used by native host-side Agent loops.

Terminal AO never imports an Agent-controlled evaluator. Harbor remains
host-owned; outer Agent processes receive only an aggregate dev capability and
cannot see the test split or test endpoint.

```bash
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters status
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters preflight
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters terminal-ao \
  --agent codex --protocol terminal-bench-2/ao_protocol/protocol.json \
  --output-dir /runs/ao/codex/seed-0 --seed 0 --dry-run
```

Agent-specific implementation notes are stored beside each checkout in
`adapter_docs/mle-bench-lite.md` and `adapter_docs/terminal-bench.md`.
