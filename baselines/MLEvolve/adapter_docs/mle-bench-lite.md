# MLEvolve — MLE-Bench Lite

Use `BenchmarkAdapters.MLEBenchLite.adapter.MleLiteAdapter` with `--agent mlevolve`. The
adapter delegates to the existing MLEvolve case in
`docker-eval/run_in_docker.sh`, sharing relay, timeout, public-data, output,
and token-log handling without copying the native search loop. The launcher
archives the tracked MLEvolve commit into a clean read-only source snapshot and
mounts only that snapshot, the current run directory, and the selected public
task directory. Submission-format validation runs as a host service, so the
Agent container never receives the full benchmark data root or private labels.
