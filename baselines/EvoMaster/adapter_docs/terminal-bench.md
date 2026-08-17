# ML-Master 2.0 — Terminal-Bench

Status: **legacy direct path disabled; current AO backend is elsewhere**.

The registered import path
`agent_adapters.ml_master_2:MLMaster2Agent` is a legacy direct-solving guard
and is intentionally disabled. The current formal Terminal AO path uses the
explicit `ml-master-autoresearch-variant` staged repository backend in
`BenchmarkAdapters/TerminalAO/`. It still requires the shared AO protocol,
clean source, model configuration and a real end-to-end smoke before formal
scoring.

The direct Harbor path must not be used for an AO score; only the shared AO
supervisor may finalize the selected solution.
