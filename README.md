# Efficient Auto-Research via Graph-Informed Thompson Sampling

An Auto-Research framework that uses Graph-Informed Thompson Sampling to efficiently solve research tasks (e.g., Kaggle/MLE-bench competitions).

## Core Idea

The search history naturally forms a tree. By adding similarity edges (via node embeddings), the tree becomes a graph. Thompson Sampling on graph-derived posteriors enables informed decisions about which direction to explore next — reducing wasted steps and token consumption.

## Architecture

```
agent/
├── engine/
│   ├── graph.py       — Search graph (Attempt nodes + derived_from + similar edges)
│   ├── thompson.py    — Thompson Sampling (posterior from graph, argmax selection)
│   ├── embedder.py    — Node embedding (concat of plan, code, metric, error)
│   ├── executor.py    — Code execution (subprocess with timeout)
│   └── search.py      — Main search loop (orchestrates all components)
├── llm/
│   └── __init__.py    — LLM API (OpenAI-compatible)
└── run.py             — CLI entry point

eval/
└── mlebench.py        — MLE-bench evaluation (runs agent + grades with official grader)
```

## Quick Start

```bash
# Set environment variables
export OPENAI_API_KEY="your-key"
export OPENAI_BASE_URL="your-endpoint"
# Model specified via --model flag (default: gpt-4o)

# Run on a single competition
python agent/run.py \
  --data_dir /path/to/competition/data \
  --desc_file /path/to/description.md \
  --output /path/to/submission.csv \
  --max_steps 50 \
  --k_neighbors 5

# Run via eval (with official mlebench grading)
python -m eval.mlebench \
  --agent agent/run.py \
  --competition spaceship-titanic \
  --data-dir /path/to/mlebench-data \
  --timeout 3600 \
  -- --max_steps 50
```

## Method

1. **Graph Construction**: Each execution step produces an Attempt node. Nodes are connected by `derived_from` edges (tree) and `similar` edges (KNN on embeddings, turning the tree into a graph).

2. **Thompson Sampling**: To select the next parent node, compute a Beta posterior for each candidate from its own children + similar neighbors' children results. Sample from each posterior, pick argmax.

3. **LLM Generation**: Given the selected parent, generate a plan (brief) then code (complete script). Execute, parse metric or error, embed the result, add to graph.

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_steps` | 50 | Maximum search steps |
| `time_limit` | 43200 | Total time budget (seconds) |
| `k_neighbors` | 5 | KNN neighbors for similar edges |
| `model` | gpt-4o | LLM model name |
| `exec_timeout` | 3600 | Per-step code execution timeout |

## Setup

```bash
# Install dependencies
pip install -e .

# For MLE-bench evaluation, also install mlebench:
pip install -e /path/to/mle-bench

# Set your LLM API credentials
export OPENAI_API_KEY="your-key"
export OPENAI_BASE_URL="your-endpoint"  # optional, defaults to https://api.openai.com/v1
```

### Requirements

- Python >= 3.11
- `openai >= 1.10.0`
- `numpy >= 1.24.0`
- `sentence-transformers >= 2.0.0`
