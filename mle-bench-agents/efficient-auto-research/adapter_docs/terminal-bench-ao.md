# EAR — Modified Terminal-Bench AO

EAR now uses the shared `BenchmarkAdapters.RepositoryAgent` runtime. The thin
launcher at `BenchmarkAdapters/RepositoryAgent/launchers/ear.py` selects a
sample-efficient hypothesis/exploration profile while the common backend owns
repository tools, isolated candidate workspaces, dev evaluation, revision
history, Relay settings, timeouts, and best-diff application.

This is a repository-mode adaptation of EAR's search discipline. It does not
claim that the upstream MLE-specific EAR engine natively implements Harbor's
repository Agent interface.
