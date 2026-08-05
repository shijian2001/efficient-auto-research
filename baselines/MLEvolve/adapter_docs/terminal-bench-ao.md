# MLEvolve — Modified Terminal-Bench AO

The shared adapter `BenchmarkAdapters.TerminalBench.adapter.TerminalAoAdapter` records MLEvolve as `blocked-native-backend`. MLEvolve's
native runtime is Kaggle/MLE-specific and does not expose a repository-editing
Harbor Agent backend. This mode fails closed until that backend is implemented.
