# UV Environment Matrix

The benchmark and Agent source trees do not all use the same Python runtime.
This directory exposes one UV-managed command for every benchmark/Agent pair.

## Policy

- Existing upstream `pyproject.toml` and `uv.lock` files remain the source of
  truth for standalone benchmark and Agent environments.
- Terminal custom Agents that run in Harbor's Python process use a locked
  combined profile under `terminal/<agent>/`.
- Arbor's MLE adapter uses `mle/arbor/`, a locked combined Arbor + shared MLE
  training environment used directly by the Docker launcher.
- EAR's MLE adapter uses `mle/ear/`, a locked combined EAR + shared MLE
  training environment used directly by the Docker launcher.
- MLEvolve uses `agents/mlevolve/`, a locked control-plane environment for its
  native search and LLM modules plus the shared CUDA 12-compatible MLE training
  stack. Installation archives the tracked baseline commit into
  `.venv/agent-source`; the runtime does not import a mutable checkout.
- Codex and Claude Code remain host CLIs and receive version checks instead of
  Python environments.
- API keys are never needed for installation and are never written here.

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
# List all fourteen benchmark/Agent profile names.
bash BenchmarkAdapters/environments/install.sh --list

# Preview one profile.
bash BenchmarkAdapters/environments/install.sh \
  terminal-bench-2.arbor --dry-run

# Install a combined custom-Agent profile.
bash BenchmarkAdapters/environments/install.sh terminal-bench-2.arbor
bash BenchmarkAdapters/environments/install.sh terminal-bench-2.ai-scientist
bash BenchmarkAdapters/environments/install.sh mle-bench-lite.arbor

# Install all profile dependencies.
bash BenchmarkAdapters/environments/install.sh all
```

The installer defaults dependency downloads to `http://127.0.0.1:17892` unless
proxy variables are already set. Override it with
`BENCHMARK_ADAPTERS_PROXY`. Project Python versions come from
`manifest.toml`.

Locks are mandatory for reproducibility and every project runs
`uv sync --locked`. The MLEvolve profile also writes a `.pth` file pointing to
its source checkout so native modules import from any working directory. The
upstream baseline checkouts remain unchanged.
