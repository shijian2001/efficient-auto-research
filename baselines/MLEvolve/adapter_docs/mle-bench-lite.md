# MLEvolve — MLE-Bench Lite

The launcher is implemented, but this is not evidence of a formal score. The
shared schema-v2 manifest, model track, clean source and real scored smoke must
be ready before this cell enters the seven-Agent comparison.

Use `BenchmarkAdapters.MLEBenchLite.adapter.MleLiteAdapter` with `--agent mlevolve`. The
adapter delegates to the existing MLEvolve case in
`docker-eval/run_in_docker.sh`, sharing relay, timeout, public-data, output,
and token-log handling without copying the native search loop. The launcher
archives the tracked MLEvolve commit into a clean read-only source snapshot and
mounts only that snapshot, the current run directory, and the selected public
task directory. Submission-format validation runs as a host service, so the
Agent container never receives the full benchmark data root or private labels.
