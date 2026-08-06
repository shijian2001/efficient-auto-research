# AiScientist — Modified Terminal-Bench AO

AiScientist now uses the shared `BenchmarkAdapters.RepositoryAgent` runtime.
Its thin launcher at
`BenchmarkAdapters/RepositoryAgent/launchers/ai_scientist.py` selects a
file-grounded analysis/implementation/validation profile. Repository tools,
workspace isolation, dev evaluation, revision tracking, Relay configuration,
timeouts, and best-diff application remain shared with the other research
Agents.

This is a repository-mode adaptation, not a claim that upstream AiScientist
currently ships a native Harbor `BaseAgent`.
