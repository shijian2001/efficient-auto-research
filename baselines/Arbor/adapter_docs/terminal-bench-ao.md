# Arbor — Modified Terminal-Bench AO

Use `BenchmarkAdapters.TerminalBench.adapter.TerminalAoAdapter` with `--agent arbor`
and `--optimize`. Arbor is the native repository-optimization backend in this
shared layer. Search only on the fixed development split; evaluate the frozen
harness on the held-out split afterward.
