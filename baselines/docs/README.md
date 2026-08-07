# Baseline Integration Notes

This directory holds central project-owned notes for baseline agents. The
benchmark-specific adapter notes requested for each Agent live inside that
Agent's checkout under `adapter_docs/`; keep the upstream source otherwise
unchanged.

Central installation records use one Markdown file per agent:

```text
baselines/
├── <AgentName>/          # Upstream source checkout; created when installed
└── docs/
    └── <agent-name>.md   # Installation and integration record
```

Each installed Agent also has:

```text
baselines/<AgentName>/
└── adapter_docs/
    ├── mle-bench-lite.md
    └── terminal-bench.md
```

The shared implementation is in `BenchmarkAdapters/`; these per-Agent files
only document the small native differences and the supported/blocked mode.

All benchmark/Agent environments are managed through
`BenchmarkAdapters/environments/README.md` and its UV installer:

```bash
bash BenchmarkAdapters/environments/install.sh --list
bash BenchmarkAdapters/environments/install.sh mle-bench-lite.arbor
```

For a planned agent, create the documentation file first and set its status to
`planned`. Create `baselines/<AgentName>/` only when the source is actually
installed. Start from [`TEMPLATE.md`](TEMPLATE.md).

The agent is ready to list in `../README.md` only after the document records:

- the canonical source URL, license, and immutable revision;
- reproducible installation and dependency commands;
- the evaluation-harness entry point and required environment variables;
- dataset, credential, network, and output isolation decisions;
- a smoke-test command and its observed result;
- all local patches or deviations from upstream.
