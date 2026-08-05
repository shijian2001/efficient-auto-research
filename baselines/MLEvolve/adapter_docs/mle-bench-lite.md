# MLEvolve — MLE-Bench Lite

Use `BenchmarkAdapters.MLEBenchLite.adapter.MleLiteAdapter` with `--agent mlevolve`. The
adapter delegates to the existing MLEvolve case in
`docker-eval/run_in_docker.sh`, sharing relay, timeout, public-data, output,
and token-log handling without copying the native search loop.
