# Codex CLI — MLE-Bench Lite

The launcher is implemented, but this is not evidence of a formal score. The
shared schema-v2 manifest, model track, clean source and real scored smoke must
be ready before this cell enters the seven-Agent comparison.

Use `BenchmarkAdapters.MLEBenchLite.adapter.MleLiteAdapter` with `--agent codex`. The
shared workspace creates a public-only `input/` view and requires a newly
created regular `submission.csv`; the Agent-specific code is only the Codex
CLI invocation. The CLI runs inside Bubblewrap with only the writable run
workspace, read-only selected public task data, the locked MLE UV runtime, and
the selected GPU visible. Host repositories, credentials, prior runs, and
private labels are outside the mount namespace. Slirp provides outbound
networking; the real API key remains in a host relay reached through a mounted
Unix socket, while Codex sees only a `proxy` placeholder.
