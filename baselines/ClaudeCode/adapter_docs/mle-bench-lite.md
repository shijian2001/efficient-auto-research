# Claude Code — MLE-Bench Lite

Use `BenchmarkAdapters.MLEBenchLite.adapter.MleLiteAdapter` with `--agent claude-code`.
Claude Code shares the generated public-only workspace contract with Codex;
only the CLI command, permission mode, and turn limit differ. Bubblewrap makes
`bypassPermissions` safe for this integration: the process sees only the
writable run workspace, read-only selected public task data, the locked MLE UV
runtime, and the selected GPU—not the host repository, credentials, prior runs,
or private labels. Slirp provides outbound networking; the real API key remains
in a host relay reached through a mounted Unix socket, while Claude sees only a
`proxy` placeholder.
