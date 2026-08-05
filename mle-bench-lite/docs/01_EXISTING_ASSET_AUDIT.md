# Existing Asset Audit

## Source

- Repository: `openai/mle-bench`
- Local path: `source/`
- Commit: `507f92e1138bb6e40dac5c6ee7a6758e6424bf97`
- Status on 2026-08-01: matches upstream `main`

## Existing Data

- Local path: `data/`
- Physical path: `/mnt/sdc/shijianwang/efficient-agent-research/mle-bench-data`
- Approximate size: 65GB
- Official Lite competitions: 22
- Existing competition directories: 20
- Non-empty prepared datasets: 19

Missing directories:

- `siim-isic-melanoma-classification`
- `the-icml-2013-whale-challenge-right-whale-redux`

Incomplete existing directory:

- `detecting-insults-in-social-commentary`: empty `raw/` and empty prepared
  public/private directories

## Existing Python Environment

The previous Conda environment is retained as `legacy-python-env/`. It is an
8.2GB lightweight research environment and intentionally omits TensorFlow. It is
not modified by this installation.
