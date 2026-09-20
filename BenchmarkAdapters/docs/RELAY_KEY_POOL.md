# Upstream key assignment per run instance

The provider-facing `BenchmarkAdapters/LLMRelay/server.py` accepts an optional
`UPSTREAM_EXTRA_KEYS_FILE`. The file must be owned by the relay user, have no
group/other permissions (use mode 600), and contain a nonempty JSON array of
additional API keys. The primary key still comes from `UPSTREAM_API_KEY` or
`OPENAI_API_KEY`. Duplicate keys are removed. Never put keys in launch arguments,
experiment manifests, prompts, or checked-in files.

Each provider-facing relay is pinned to one configured key using the 1-based
`UPSTREAM_KEY_SLOT`. Multi-key configurations require an explicit slot. The
campaign assigns new run instances to relay lanes in A/B order and persists
that assignment. Every request, subagent request, and retry in that instance
uses the same key. A 429, timeout, or 5xx does not switch the instance to another
key. Requests execute concurrently; existing retry limits and non-retryable 4xx
handling remain unchanged. Upstream keys are redacted from error messages.
An account-wide/provider-wide quota may still limit both keys together; adding
a key does not guarantee a throughput increase.

`/health` reports `upstream_key_count`, `upstream_key_slot`, and `upstream_key_attempts`; telemetry records
only the numeric `upstream_key_slot`. Restart a relay to load a changed key file.
The file path is stripped from Agent environments. Configure the extra file only
on the provider-facing relay: per-run relays talking to a central relay should
keep the central relay's client credential, not provider keys.

The September 20 five-instance campaign uses separate central relays on
127.0.0.1:6202 (key A) and 127.0.0.1:6203 (key B), keeping existing 6201 clients
uninterrupted. Each instance's model track is a copy of the previous one with
the relay URL changed; model, reasoning settings,
temperature, retry policy, and 12-hour task budget are unchanged. The two keys
must separately pass a small readiness request before any campaign task starts.

Confirmed run instances and fixed assignments:

| Agent | Competition | Key slot |
|---|---|---|
| ML-Master-2 | aerial-cactus-identification | A / 1 |
| ML-Master-2 | aptos2019-blindness-detection | B / 2 |
| AI Scientist | aerial-cactus-identification | A / 1 |
| AI Scientist | aptos2019-blindness-detection | B / 2 |
| AI Scientist | denoising-dirty-documents | A / 1 |

Key assignment is per instance, including when two different Agents work on the
same competition. Separate output directories prevent shared-task collisions.

The campaign restores each generated script's own worker count. It does not set
`NUMPY_MADVISE_HUGEPAGE`, preserving the previous NumPy default as requested.
There is no fixed 90-minute candidate cap: the Agent's requested command timeout
and the remaining whole-run budget apply, with 90 seconds for publication.
