# Runbook

Run commands from the installation root.

## Inspect and Verify

```bash
./scripts/show_status.sh
./scripts/verify_installation.sh
```

## Reinstall Harbor

```bash
./scripts/install_harbor.sh
```

Dependencies are pinned by `pyproject.toml` and `uv.lock`.

## Re-download and Validate the Dataset

```bash
./scripts/download_dataset.sh
./scripts/validate_dataset.sh
```

The failed partial download from the newer registry API is preserved under
`datasets/_failed-new-registry-partial-20260801` for diagnosis only. Harbor runs
must use `datasets/terminal-bench-2`.

## Test Docker and the Verifier

```bash
./scripts/run_oracle_smoke.sh
```

## Run One Task With Your Agent

```bash
TB2_AGENT='agent_adapters.my_agent:MyAgent' \
TB2_MODEL='provider/model-name' \
TB2_INCLUDE_TASK='openssl-selfsigned-cert' \
TB2_ATTEMPTS=1 \
TB2_CONCURRENCY=1 \
./scripts/run_custom_agent.sh
```

## Run Codex With the Validated Relay

The validated configuration uses Harbor's built-in `codex` Agent, model
`gpt-5.5`, and a temporary mode-`0600` Codex `auth.json`. See
`docs/11_CODEX_RELAY_VALIDATION_20260801.md` for the complete command, security
cleanup, build-proxy note, and recorded verifier result.

## Run All 89 Tasks

```bash
TB2_AGENT='agent_adapters.my_agent:MyAgent' \
TB2_MODEL='provider/model-name' \
TB2_ATTEMPTS=1 \
TB2_CONCURRENCY=4 \
./scripts/run_custom_agent.sh
```

For an official-style five-attempt run, set `TB2_ATTEMPTS=5`. Start with one
task and one attempt because a full run can consume substantial API time, Docker
storage, CPU, and memory.

## Direct Harbor Access

```bash
source scripts/env.sh
harbor-local run --help
```
