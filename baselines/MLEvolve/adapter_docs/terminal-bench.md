# MLEvolve — Terminal-Bench

Status: **legacy direct path disabled; current AO backend is elsewhere**.

The registered import path
`agent_adapters.mlevolve:MLEvolveTerminalAgent` is a legacy direct-solving
guard and is intentionally disabled. The current formal Terminal AO path uses
the native MLEvolve UCT repository backend in `BenchmarkAdapters/TerminalAO/`.
It still requires the shared AO protocol, clean source, model configuration and
a real end-to-end smoke before formal scoring.

The direct Harbor path must not be used for an AO score; only the shared AO
supervisor may select and replay the best candidate.
