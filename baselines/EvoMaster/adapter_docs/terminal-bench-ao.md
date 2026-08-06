# ML-Master 2.0 — Modified Terminal-Bench AO

ML-Master 2.0 now uses the shared `BenchmarkAdapters.RepositoryAgent` runtime.
The thin launcher at `BenchmarkAdapters/RepositoryAgent/launchers/ml_master_2.py`
selects a research-engineering profile; all repository tools, candidate
revisions, dev scoring, sandboxing, Relay settings, and best-diff application are
implemented once in the shared backend.

This avoids reusing the MLE-specific ML-Master playground and does not claim an
upstream native Harbor `BaseAgent` implementation.
