# Efficient Auto-Research via Kernel Thompson Sampling

An Auto-Research framework that formulates LLM agent search as GP Regression with Kernel Thompson Sampling on the search tree, achieving principled and efficient parent selection.

## Core Idea

LLM agent search produces a tree of attempts (each derived from a parent). We model "what metric can this node's children achieve?" as GP Regression with a cosine kernel on plan+code embeddings. Kernel Thompson Sampling on the exact GP posterior selects the next parent — one observation informs all similar nodes via kernel correlation, reducing the total steps needed.

## Method

1. **Search Tree**: Each step produces an Attempt node. `derived_from` edges form a tree. Each node's children produce continuous observations (their metric values).

2. **GP Regression**: Prior $f \sim \mathcal{N}(0, K)$ where $K_{ij} = \cos(\text{emb}_i, \text{emb}_j)$. Observation: $y_{ij} = \text{child}_j\text{.metric}$. Posterior is exact closed-form (no approximation needed).

3. **Kernel Thompson Sampling**: Sample jointly from the exact posterior → argmax selects the parent whose children are expected to have the highest metric. Kernel correlation ensures one observation updates beliefs about all similar nodes.

4. **LLM Generation**: Given the selected parent, generate a plan then code. Execute, parse result, compute embedding, add to tree.

## Why It's Efficient

- **Greedy (baseline)**: Always selects the highest-metric node → gets stuck at local optima.
- **UCT (MLEvolve)**: Needs many visits per node to converge → wastes steps.
- **Kernel TS (ours)**: One observation propagates to all similar nodes via GP posterior → fewer steps to make informed decisions.

No heuristic thresholds. No time-based phase switching. Exact posterior (no approximation).

## Architecture

```
agent/
├── engine/
│   ├── graph.py       — Search tree (Attempt nodes + derived_from edges + kernel matrix)
│   ├── thompson.py    — Kernel Thompson Sampling (GP Regression + exact posterior + joint sampling)
│   ├── embedder.py    — Node embedding (plan + code)
│   ├── executor.py    — Code execution (subprocess with timeout + process group kill)
│   └── search.py      — Main search loop
├── llm/
│   └── __init__.py    — LLM API (OpenAI-compatible)
└── run.py             — CLI entry point

eval/
└── mlebench.py        — MLE-bench evaluation (runs agent + official grading)
```

## Quick Start

```bash
# Set LLM credentials
export OPENAI_API_KEY="your-key"
export OPENAI_BASE_URL="your-endpoint"

# Run on a single task
python agent/run.py \
  --data_dir /path/to/competition/data \
  --desc_file /path/to/description.md \
  --output /path/to/submission.csv \
  --max_steps 50 \
  --model gpt-4o

# Run with official MLE-bench grading
python -m eval.mlebench \
  --agent agent/run.py \
  --competition spaceship-titanic \
  --data-dir /path/to/mlebench-data \
  --timeout 3600 \
  -- --max_steps 50
```

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_steps` | 50 | Maximum search steps |
| `time_limit` | 43200 | Total time budget (seconds) |
| `model` | gpt-4o | LLM model name |
| `exec_timeout` | 3600 | Per-step code execution timeout |

## Setup

```bash
pip install -e .

# For MLE-bench evaluation:
pip install -e /path/to/mle-bench

export OPENAI_API_KEY="your-key"
export OPENAI_BASE_URL="your-endpoint"  # optional, defaults to https://api.openai.com/v1
```

### Requirements

- Python >= 3.11
- `openai >= 1.10.0`
- `numpy >= 1.24.0`
- `sentence-transformers >= 2.0.0`
- `tf-keras >= 2.16.0`

## References

- Rasmussen & Williams, 2006. Gaussian Processes for Machine Learning, Ch.2 (GP Regression).
- Chowdhury & Gopalan, 2017. On Kernelized Multi-armed Bandits (Kernel TS regret bound).
