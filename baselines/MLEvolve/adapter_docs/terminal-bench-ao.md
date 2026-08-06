# MLEvolve — Modified Terminal-Bench AO

MLEvolve now uses `BenchmarkAdapters.RepositoryAgent`, the project-wide
repository-optimization backend. Its thin launcher is
`BenchmarkAdapters/RepositoryAgent/launchers/mlevolve.py`; it contributes only
the draft/debug/improve/evolve strategy profile. Workspace isolation, tools,
dev evaluation, revision history, best-candidate materialization, Relay settings,
timeouts, and held-out protection are shared.

This is a repository-mode adaptation of MLEvolve's evolutionary strategy. It is
not represented as an upstream native Harbor `BaseAgent`, and it does not reuse
MLEvolve's Kaggle-only submission executor.
