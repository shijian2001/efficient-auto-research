# Contributing

Thanks for your interest in improving Arbor. This page covers the basics of working on the
codebase and the documentation.

## Development setup

```bash
git clone https://github.com/RUC-NLPIR/Arbor.git
cd Arbor
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

Verify your environment:

```bash
arbor doctor
```

## Project layout

The source lives in `src/` and is imported as the `arbor` package:

```text
src/
├── cli/            # Typer CLI: commands, intake chat, dashboard
├── coordinator/    # the research director: idea tree, orchestrator, tools
├── core/           # agent loop, LLM providers, shared tools, config
├── executor/       # the research engineer that runs one experiment
├── events/         # event bus + subscribers (logging, stats)
├── plugins/        # domain plugins (e.g. mle_kaggle.yaml)
├── report/         # REPORT.md generation
├── search_agent/   # literature/search agent
├── skills/         # markdown skill playbooks
└── webui/          # read-only browser monitor
```

!!! note "Packaging detail"
    The on-disk directory is `src/`, but it is installed and imported as `arbor`
    via a `package-dir` mapping in `pyproject.toml`. When you add a **new sub-package**
    (a new directory with an `__init__.py`), add it to the explicit `packages` list in
    `pyproject.toml` so it ships in the wheel.

## Working on the docs

The documentation site is built with [MkDocs Material](https://squidfunk.github.io/mkdocs-material/).

```bash
pip install -e ".[docs]"     # install docs dependencies
mkdocs serve                 # live-reload preview at http://127.0.0.1:8000
mkdocs build                 # produce the static site in ./site
```

Documentation sources are markdown files in `docs/`, with navigation defined in
`mkdocs.yml`. To add a page, create the markdown file and add it to the `nav:` tree.

## Submitting changes

1. Create a branch for your change.
2. Keep changes focused; match the surrounding code style.
3. Verify the CLI still works (`arbor version`, `arbor doctor`) and, if you touched docs,
   that `mkdocs build` is clean.
4. Open a pull request describing the motivation and the change.

## Citation

If you use Arbor in your research, please cite the paper:

```bibtex
@misc{jin2026arbor,
  title  = {Toward Generalist Autonomous Research via Hypothesis-Tree Refinement},
  author = {Jiajie Jin and Yuyang Hu and Kai Qiu and Qi Dai and Chong Luo and
            Guanting Dong and Xiaoxi Li and Tong Zhao and Xiaolong Ma and
            Gongrui Zhang and Zhirong Wu and Bei Liu and Zhengyuan Yang and
            Linjie Li and Lijuan Wang and Hongjin Qian and Yutao Zhu and Zhicheng Dou},
  year   = {2026},
  eprint = {2606.11926},
  archivePrefix = {arXiv},
  url    = {https://arxiv.org/abs/2606.11926}
}
```
