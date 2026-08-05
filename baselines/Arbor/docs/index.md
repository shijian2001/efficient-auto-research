---
hide:
  - navigation
---

# Arbor

*Toward Generalist Autonomous Research via Hypothesis-Tree Refinement*

**Arbor is an autonomous research agent that turns a long-horizon objective into a
cumulative search.** Give it a benchmark and a goal; it proposes hypotheses, edits code,
runs real experiments, learns from the results, and keeps the improvements that hold up
on held-out data.

Instead of one-shot attempts that forget what failed, Arbor grows a **hypothesis tree**:
every idea becomes a branch — pruned if it fails, harvested if it works — and insights
propagate back up the tree so later ideas start smarter.

<div class="grid cards" markdown>

-   :material-rocket-launch: **Get running in minutes**

    `pip install arbor-agent`, `arbor setup`, then `arbor`.

    [:octicons-arrow-right-24: Installation](installation.md)

-   :material-flask: **Run your first study**

    Point Arbor at a benchmark and watch the Idea Tree grow.

    [:octicons-arrow-right-24: Quickstart](quickstart.md)

-   :material-sitemap: **Understand the method**

    The arbor cycle, the Idea Tree, git isolation, and held-out discipline.

    [:octicons-arrow-right-24: How It Works](how-it-works.md)

-   :material-tune: **Configure everything**

    Providers, budgets, timeouts, and human-in-the-loop modes.

    [:octicons-arrow-right-24: Configuration](configuration.md)

</div>

## Two cooperating agents

| Agent | Role |
| --- | --- |
| **Coordinator** | The research director. Maintains the Idea Tree, drives the search via the *arbor cycle*, and dispatches experiments. |
| **Executor** | The research engineer. Given one idea, it implements the code changes, runs the experiment in an isolated git worktree, and reports evidence. |

## Why Arbor

- **Grows evidence, not logs.** Results, failure modes, and distilled insights live in a
  persistent Idea Tree — not a scrollback buffer.
- **Held-out discipline by default.** Executors iterate on a dev split; only improvements
  that clear a configurable margin on a held-out test split are merged.
- **Isolated, reversible experiments.** Every experiment runs in its own git worktree on
  a dedicated branch. Your `main` is never touched until you merge.
- **Backpropagated insight.** After each experiment, an LLM abstracts what was learned and
  pushes it up the tree, so sibling and descendant ideas inherit hard-won context.
- **Use any model.** Anthropic, OpenAI / Responses API, or anything OpenAI-compatible
  through LiteLLM (DeepSeek, Gemini, Qwen, vLLM, Ollama, local gateways).
- **Domain adaptation without code changes.** A one-line `plugin:` retargets the agent;
  Skills are markdown playbooks loaded on demand.

!!! tip "New here?"
    Start with [Installation](installation.md) → [Quickstart](quickstart.md), then read
    [How It Works](how-it-works.md) to understand the moving parts.
