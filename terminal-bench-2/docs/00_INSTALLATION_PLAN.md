# Installation Plan

Date: 2026-08-01

## Objective

Install Harbor and Terminal-Bench 2.0 in a self-contained directory under the
current research workspace, while retaining all commands, logs, configuration,
and operating documentation in that directory.

## Steps

1. Audit the host system and Docker runtime.
2. Pin the latest stable Harbor release.
3. Create a local Python 3.12 environment with uv.
4. Download the official Terminal-Bench 2.0 dataset.
5. Retain an immutable source snapshot and dataset manifest.
6. Validate task manifests and resource declarations.
7. Run an Oracle smoke test in Docker.
8. Document custom-agent integration and full benchmark operation.

## Version Decisions

- Harbor: `0.20.0`
- Python: `3.12.13`
- Dataset: official Terminal-Bench 2.0 compatibility identifier `terminal-bench@2.0`
- Execution backend: local Docker

Harbor is the execution and grading harness. The evaluated agent remains this
repository's own agent and will be connected through a custom Harbor adapter.

All eight steps were completed on 2026-08-01. The final machine-readable check
is recorded in `config/install_verification.json`.
