# BenchmarkAdapters

Canonical location for the project-owned benchmark adapters.

```text
BenchmarkAdapters/
├── pyproject.toml
├── MLEBenchLite/
│   ├── __init__.py
│   └── adapter.py
├── TerminalBench/
│   ├── __init__.py
│   └── adapter.py
├── RepositoryAgent/    # shared Terminal AO backend + thin research profiles
├── contracts.py       # shared request/command contracts
├── process.py         # shared process and relay environment
├── registry.py        # baseline installation and capability registry
├── agents.py          # one facade across both benchmark contracts
├── cli.py             # `python -m BenchmarkAdapters`
├── api_smoke.py       # common API/native-agent smoke
├── relay.py           # local relay process supervisor
└── status.py          # installation inventory
```

The benchmark-specific implementation is deliberately separated:

- `BenchmarkAdapters/MLEBenchLite/adapter.py` handles public task workspaces,
  native Docker launchers, generic CLI workspaces, and submission recovery.
- `BenchmarkAdapters/TerminalBench/adapter.py` handles the modified
  Terminal-Bench Harness-Engineering AO protocol, including dev/test split
  evaluation. Codex, Claude Code, and Arbor use their repository-native CLIs;
  EAR, MLEvolve, ML-Master 2.0, and AiScientist use the shared isolated
  repository backend with thin Agent-specific strategy profiles.

Run either benchmark adapter through the canonical package:

```bash
python -m BenchmarkAdapters mle --agent arbor \
  --competition-id spooky-author-identification \
  --data-root /path/to/mle-bench-data \
  --output-dir /path/to/run \
  --dry-run

python -m BenchmarkAdapters terminal --agent codex \
  --harness-dir /path/to/terminus-2 \
  --eval-script /path/to/run_eval.py \
  --dev-data /path/to/data/dev.json \
  --test-data /path/to/data/test.json \
  --output-dir /path/to/ao-run \
  --split dev \
  --dry-run
```

The lowercase `benchmark_adapters` package remains as a compatibility wrapper
for existing commands and imports.

Environment installation is managed under `BenchmarkAdapters/environments/`.
Use its `manifest.toml` and `install.sh` for UV-managed benchmark/Agent setup.
