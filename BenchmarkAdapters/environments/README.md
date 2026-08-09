# UV Environment Matrix

The benchmark and Agent source trees do not all use the same Python runtime.
This directory exposes one UV-managed command for every benchmark/Agent pair,
plus standalone benchmark runtimes shared by the seven-Agent adapters.

## Policy

- Existing upstream `pyproject.toml` and `uv.lock` files remain the source of
  truth for standalone benchmark and Agent environments.
- Terminal custom Agents that run in Harbor's Python process use a locked
  combined profile under `terminal/<agent>/`.
- Arbor's MLE adapter uses `mle/arbor/`, a locked combined Arbor + shared MLE
  training environment used directly by the Docker launcher.
- EAR's MLE adapter uses `mle/ear/`, a locked combined EAR + shared MLE
  training environment used directly by the Docker launcher.
- MLEvolve uses `agents/mlevolve/` for MLE Bench and the smaller
  `agents/mlevolve-autoresearch/` control-plane runtime for Autoresearch.
  Installation archives the tracked baseline commit into `.venv/agent-source`;
  the runtime does not import a mutable checkout.
- Autoresearch uses minimal EAR and ML-Master 2 control-plane environments plus
  the existing locked Arbor and AiScientist combined environments. All five
  Python Agent profiles archive tracked source into `.venv/agent-source`, and
  the reusable Autoresearch launchers import only those read-only snapshots.
- Optimizer Design reuses those same seven locked Agent runtimes through tiny
  per-Agent adapters, while its four-H100 benchmark runtime is independently
  frozen by `optimizer-design/uv.lock` and its environment manifest.
- Codex and Claude Code remain host CLIs and receive version checks instead of
  Python environments.
- API keys are never needed for installation and are never written here.
- `autoresearch` is the standalone locked Architecture Design benchmark runtime;
  Agent adaptation remains in the separate shared launcher/runtime profiles.
- `optimizer-design` is the standalone locked modded-NanoGPT Optimizer Design
  benchmark runtime used by the two-layer Adapter.

Current combined Terminal profiles:

| Profile | Contents | Reason |
|---|---|---|
| `terminal/arbor` | Harbor `0.20.0` + editable Arbor | imports Arbor's native ReAct loop |
| `terminal/ai-scientist` | Harbor `0.20.0` + editable AiScientist | imports AiScientist's native Subagent loop |

Current combined MLE profile:

| Profile | Contents | Reason |
|---|---|---|
| `mle/arbor` | editable Arbor + shared MLE training stack | launcher and installation use the same Python runtime |
| `mle/ear` | packaged EAR + shared MLE training stack | removes the launcher's legacy Conda dependency |

EAR, MLEvolve, and ML-Master 2 still install their standalone Agent and common
Terminal environments, but their Terminal commands remain fail-closed until
the required candidate/workspace backends are complete.

## One-Click Commands

```bash
# List all fourteen benchmark/Agent profiles plus standalone benchmarks.
bash BenchmarkAdapters/environments/install.sh --list

# Preview one profile.
bash BenchmarkAdapters/environments/install.sh \
  terminal-bench-2.arbor --dry-run

# Install a combined custom-Agent profile.
bash BenchmarkAdapters/environments/install.sh terminal-bench-2.arbor
bash BenchmarkAdapters/environments/install.sh terminal-bench-2.ai-scientist
bash BenchmarkAdapters/environments/install.sh mle-bench-lite.arbor

# Install standalone benchmark runtimes.
bash BenchmarkAdapters/environments/install.sh autoresearch
bash BenchmarkAdapters/environments/install.sh optimizer-design

# Install all profile dependencies.
bash BenchmarkAdapters/environments/install.sh all
```

The installer defaults dependency downloads to `http://127.0.0.1:17892` unless
proxy variables are already set. Override it with
`BENCHMARK_ADAPTERS_PROXY`. Project Python versions come from
`manifest.toml`; a host with a preinstalled interpreter can override one with
`BENCHMARK_ADAPTERS_PYTHON_311` or `BENCHMARK_ADAPTERS_PYTHON_312`.

Locks are mandatory for reproducibility and every project runs
`uv sync --locked`. The MLEvolve profile also writes a `.pth` file pointing to
its source checkout so native modules import from any working directory. The
upstream baseline checkouts remain unchanged.
