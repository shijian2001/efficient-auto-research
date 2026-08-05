# Terminal-Bench 2.0 Local Installation

This directory contains a self-contained installation of Harbor and the
Terminal-Bench 2.0 dataset for evaluating this repository's own agent.

## Installation Status

- Harbor version: `0.20.0` (pinned)
- Python version: `3.12.13`
- Dataset: Terminal-Bench 2.0, `89/89` tasks installed and validated
- Docker verification: Oracle smoke test passed with reward `1.0`
- Custom agent integration: launcher and adapter directory installed

## Directory Layout

- `.venv/`: local Harbor Python environment
- `datasets/`: downloaded Terminal-Bench data and source snapshot
- `jobs/`: Harbor job outputs
- `results/`: exported summaries and reports
- `agent_adapters/`: adapters for this repository's own agent
- `scripts/`: reproducible installation and run commands
- `docs/`: installation, operation, and integration documentation
- `logs/`: raw command output from every installation step
- `config/`: pinned manifests and local configuration

## Local Harbor Command

```bash
source scripts/env.sh
harbor-local --version
```

## Verify Installation

```bash
./scripts/verify_installation.sh
```

## Oracle Smoke Test

```bash
./scripts/run_oracle_smoke.sh
```

## Run a Custom Agent

```bash
TB2_AGENT='agent_adapters.my_agent:MyAgent' \
TB2_MODEL='model-name' \
TB2_INCLUDE_TASK='openssl-selfsigned-cert' \
./scripts/run_custom_agent.sh
```

See `docs/08_RUNBOOK.md` for normal operation and `docs/02_INSTALLATION_LOG.md`
for the complete installation record.
