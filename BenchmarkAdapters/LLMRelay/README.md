# Repository-owned LLM relay

Every real Agent process launched by the five benchmark adapters talks to this
relay rather than directly to an upstream model endpoint. The host supervisor
keeps the real upstream credential, forces the reviewed model configuration,
records token usage, and exposes only a run-local loopback or Unix-socket
endpoint with the placeholder credential `proxy` to the Agent.

The service is launched per run by `RelayProcess`; it is not a shared daemon.
Docker MLE runs launch the same `server.py` on the host and expose it to the
container through the Docker bridge.

Supported downstream request shapes are Messages (`/v1/messages`), OpenAI Chat
Completions (`/v1/chat/completions`), and OpenAI Responses (`/v1/responses`).
They are normalized inside the relay. The only upstream request formats are
OpenAI-compatible Chat Completions or Responses, selected by the reviewed model
configuration. The relay does not send a provider-specific Messages protocol
upstream.

The frozen model track owns all model-generation parameters. Client-supplied
sampling, reasoning, logprobability, structured-output, seed, and parallel-tool
parameters are stripped before the track is injected. `messages`, `tools`, and
`tool_choice` remain protocol payload so a native tool Agent can express its
actions without changing the shared model configuration.

For `tool_choice=auto`, an upstream text response is returned unchanged. For an
explicitly required function call, a response without `tool_calls` is returned
as a protocol error. The relay never asks for text JSON and never synthesizes a
tool call on the Agent's behalf.
