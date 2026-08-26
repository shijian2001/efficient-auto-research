"""Typed payload schemas for the EventBus — the machine-checkable face of
the event contract (D1, contract 2).

Every event's ``data`` dict is the coupling surface between the engine (member
A, which emits) and the observability/HITL surfaces (member B, which renders).
These ``TypedDict``s document the exact keys each event carries so both sides
build against one spec instead of guessing.

Two hard rules for this contract:

1. **Append-only.** Never rename an event constant in :mod:`events.types` or
   drop a payload key — external tools (file_logger, dashboards, WebUI) join on
   the string values. Adding optional keys is fine.
2. **JSON-serializable.** Payloads are forwarded over SSE / WebSocket by the
   WebUI (#7) and persisted to ``events.jsonl``. Values must be JSON-native
   (str / int / float / bool / None / list / dict). Use
   :func:`assert_json_serializable` in tests to enforce this. Never put secrets
   (api_key / base_url) in a payload — see ``config_schema.SENSITIVE_KEYS``.
"""

from __future__ import annotations

import json
from typing import Any, TypedDict

from . import types as E


# ── Streaming reasoning / tool activity (#6 tree viz, #7 WebUI) ──────────────

class ThinkingDelta(TypedDict):
    """``THINKING_DELTA`` — one chunk of streamed model reasoning."""

    node_id: str            # tree node the agent is working on ("" for coordinator root)
    text: str               # incremental thinking text (append to prior deltas)
    agent: str              # "coordinator" | "sub:<node_id>" | "search"


class ToolStart(TypedDict):
    """``TOOL_START`` — a tool call began."""

    name: str               # tool name, e.g. "Bash", "RunTraining"
    args_preview: str       # short, truncated, secret-free rendering of args
    agent: str
    node_id: str


class ToolEnd(TypedDict):
    """``TOOL_END`` — a tool call finished."""

    name: str
    ok: bool                # True on success, False on error/timeout
    duration: float         # wall-clock seconds
    output_preview: str     # short, truncated rendering of the result
    agent: str
    node_id: str


# ── Cache governance (#13) ──────────────────────────────────────────────────

class CacheStat(TypedDict):
    """``CACHE_STAT`` — aggregate KV-cache accounting for one LLM call.

    ``LLM_CALL`` already carries per-call ``cache_*`` fields; this aggregate is
    emitted when #13 wants a rolled-up hit/miss view for the dashboard.
    """

    cache_read: int         # tokens served from cache (a hit)
    cache_write: int        # tokens written to cache this call
    miss: int               # uncached tokens with no cache involvement
    total: int              # cache_read + cache_write + miss (= logical input tokens)


# ── Human-in-the-loop (#2 gating, #10 ask-back, #11 quick commands) ──────────

class AwaitUser(TypedDict):
    """``AWAIT_USER`` — the engine is blocked waiting for a human decision.

    The renderer collects input and replies via ``USER_INPUT_RECEIVED``. This
    payload is also what a checkpoint stores in ``pending_user`` (contract 3),
    so a run can be resumed mid-question.
    """

    kind: str               # "idea_review" | "idea_proposal_review" | "idea_direction" | "ask_back" | "command" | ...
    prompt: str             # human-facing question / instruction
    node_id: str            # related tree node ("" if not node-scoped)
    options: list[str]      # suggested choices ([] = free-form text)


# ── Session / checkpoint (#1 resume, #12 report) ────────────────────────────

class CheckpointSaved(TypedDict):
    """``CHECKPOINT_SAVED`` — a run checkpoint was written atomically."""

    path: str               # path to the checkpoint JSON
    cycle: int              # cycle_num captured in the checkpoint
    reason: str             # "cycle_end" | "pre_executor" | "shutdown" | ...


# ── Progress heartbeat (#8 long-stability) ──────────────────────────────────

class Heartbeat(TypedDict):
    """``HEARTBEAT`` — periodic liveness while an agent blocks on a long phase."""

    agent: str              # agent label, e.g. "coordinator" | "sub:1.2"
    node_id: str            # tree node this work belongs to ("" if none)
    operation: str          # "llm" | "tool:<names>"
    elapsed_seconds: float  # seconds since the current phase began
    detail: str             # short human note (e.g. the tool name list)


#: Map of the contract-2 event constants to their payload TypedDicts. Keyed by
#: the constants themselves (not string literals) so the values can never drift
#: from :mod:`events.types`.
PAYLOAD_SCHEMAS: dict[str, type] = {
    E.THINKING_DELTA: ThinkingDelta,
    E.TOOL_START: ToolStart,
    E.TOOL_END: ToolEnd,
    E.CACHE_STAT: CacheStat,
    E.AWAIT_USER: AwaitUser,
    E.CHECKPOINT_SAVED: CheckpointSaved,
    E.HEARTBEAT: Heartbeat,
}


def assert_json_serializable(data: Any) -> None:
    """Raise ``TypeError`` if ``data`` is not JSON-serializable as-is.

    The contract forbids relying on the file logger's ``default=str`` fallback:
    payloads must survive a strict ``json.dumps`` so the WebUI can forward them
    over SSE/WebSocket unchanged.
    """
    json.dumps(data, allow_nan=False)
