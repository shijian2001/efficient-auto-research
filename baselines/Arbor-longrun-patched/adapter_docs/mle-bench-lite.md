# Arbor — MLE-Bench Lite

The launcher is implemented, but this is not evidence of a formal score. The
MLE cell requires the explicit `arbor-benchmark-patched` variant plus the shared
schema-v2 manifest, model track, clean source and real scored smoke.

Use `BenchmarkAdapters.MLEBenchLite.adapter.MleLiteAdapter` with `--agent arbor`. The
adapter delegates to the existing Arbor Docker case and preserves Arbor's
hash-bound submission recovery and external official grading.
