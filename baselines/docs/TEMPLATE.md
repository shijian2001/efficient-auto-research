# <Agent Name>

- **Status:** planned | installing | integrated | validated
- **Source:** <canonical repository URL>
- **License:** <license and version>
- **Pinned revision:** <tag or full commit SHA>
- **Checkout:** `baselines/<AgentName>/`
- **Owner:** <maintainer>
- **Last verified:** YYYY-MM-DD

## Purpose

Explain why this agent is included and which published baseline or result it
represents.

## Installation

Record commands that reproduce the checkout and environment from a clean
machine. Include the supported Python, CUDA, and system-package versions.

```bash
# Example only; replace with exact commands.
git clone <repository-url> baselines/<AgentName>
git -C baselines/<AgentName> checkout <full-commit-sha>
```

## Harness Integration

Document:

- the launcher or adapter entry point;
- required and optional environment variables;
- LLM relay compatibility and model settings;
- public dataset mounts and private-data isolation;
- output, trace, and token-log locations;
- timeout, step, CPU, RAM, and GPU defaults.

## Validation

Provide the smallest smoke test that proves the agent starts, accesses only the
intended resources, calls the configured LLM endpoint, and writes a valid
submission.

```bash
# Smoke-test command
```

Record the date, pinned revision, task, result, and relevant log path.

## Upstream Deviations

List every local patch, configuration override, or behavior difference from
the pinned upstream revision. If there are none, state that explicitly.

## Known Issues

Record unresolved compatibility, reproducibility, or fairness concerns.

## Benchmark Runs

Link validated run manifests, grading reports, and result summaries. Do not
treat local holdout metrics as official MLE-Bench scores.
