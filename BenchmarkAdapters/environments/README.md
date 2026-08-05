# UV Environment Matrix

The benchmark and Agent source trees do not all use the same Python runtime.
This directory provides one UV-managed entry point for every benchmark/Agent
pair without duplicating the large dependency graph fourteen times.

## Existing manifests

| Component | Current state | Installer behavior |
|---|---|---|
| `mle-bench-lite` | `pyproject.toml` + `uv.lock` | `uv sync --frozen` |
| `terminal-bench-2` | `pyproject.toml` + `uv.lock` | `uv sync --frozen` |
| Arbor | `pyproject.toml` + `uv.lock` | `uv sync --frozen` |
| AiScientist | `pyproject.toml` + `uv.lock` | `uv sync --frozen` |
| EAR | `pyproject.toml`, lock may be absent | generate `uv.lock`, then sync |
| ML-Master 2.0 | `pyproject.toml`, lock may be absent | generate `uv.lock`, then sync |
| MLEvolve | pinned `requirements_*.txt` | `uv pip sync` through tracked pinned includes |
| Codex / Claude Code | host CLI, not Python packages | version check only |

The source of truth is `manifest.toml`. It defines two benchmark projects and
seven Agent environment strategies. A profile installs the benchmark project
and the Agent runtime; repeated profiles reuse the already-managed project
environment rather than creating identical copies.

The source-tree `uv.lock` files are used for MLE-Bench Lite, Terminal-Bench,
Arbor, AiScientist, EAR, and ML-Master 2.0. MLEvolve keeps its upstream pinned
requirements through `requirements/mlevolve.lock`, avoiding a fresh resolver
run over its large ML stack.

## One-click commands

From the repository root:

```bash
# Preview without changing environments.
bash BenchmarkAdapters/environments/install.sh \
  mle-bench-lite.arbor --dry-run

# Install one pair.
bash BenchmarkAdapters/environments/install.sh \
  mle-bench-lite.arbor

# Install a Terminal-Bench/Codex pair.
bash BenchmarkAdapters/environments/install.sh \
  terminal-bench-2.codex

# List all fourteen benchmark/Agent profiles.
bash BenchmarkAdapters/environments/install.sh --list

# Install all profiles.
bash BenchmarkAdapters/environments/install.sh all
```

The default UV bootstrap interpreter is Python 3.11. Override it with
`UV_INSTALLER_PYTHON=3.12` if the local UV installation should use another
interpreter for the installer itself. Benchmark and Agent project Python
versions come from `manifest.toml`.

The installer defaults package downloads to `http://127.0.0.1:17892` unless
proxy variables are already set. Override it with `BENCHMARK_ADAPTERS_PROXY`.
API keys are not needed for dependency installation and are never written by
the installer.
