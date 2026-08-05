"""Read-only Q&A companion for the live dashboard (#11).

Lets the user ask questions mid-run *without* disturbing the research agent.
A separate, read-only :class:`~arbor.core.agent.Agent` is grounded in a
snapshot of the main agent's conversation — read from the checkpoint's
``messages.jsonl`` (member A's #1), served as data — plus read-only tools to
inspect the live workspace and the research state on demand.

Isolation (so the companion never pollutes the research run):

* its own provider instance and a NullBus (no events on the shared bus),
* ``track_stats=False`` so its tokens/cache don't enter the run's AgentStats,
* read-only tools only (no Bash, ``auto_git=False``) — it cannot mutate state,
* its own asyncio loop in a daemon thread, fully decoupled from the
  orchestrator's loop.

The conversation panel routes plain questions here; ``/steer`` is the separate,
explicit path that injects into the research agent (``RunState.push_user_message``).
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from collections import deque
from pathlib import Path
from typing import Any, Callable

from ..core.agent import Agent
from ..core.config import AgentConfig
from ..core.llm.base import LLMProvider
from ..core.tools.base import Tool
from ..core.tools.file_read import FileReadTool
from ..core.tools.glob_tool import GlobTool
from ..core.tools.grep import GrepTool
from ..coordinator.checkpoint import read_messages, seal_interrupted_tail
from .run_state import RunState

_MAX_TRANSCRIPT_CHARS = 80_000
_MAX_BLOCK_CHARS = 2_000


# ── transcript serialization (history as DATA, not the companion's turns) ────


def _render_block(block: Any) -> str:
    """Render one message-content block as readable text, tolerant of both
    Anthropic-style block dicts and plain strings/other providers."""
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return str(block)
    btype = block.get("type")
    if btype == "text":
        return block.get("text", "")
    if btype == "thinking":
        return "[thinking] " + (block.get("thinking") or block.get("text") or "")
    if btype == "tool_use":
        args = json.dumps(block.get("input"), ensure_ascii=False)
        return f"[tool_call {block.get('name')}] {args[:_MAX_BLOCK_CHARS]}"
    if btype == "tool_result":
        content = block.get("content")
        if isinstance(content, list):
            content = " ".join(_render_block(b) for b in content)
        return f"[tool_result] {str(content)[:_MAX_BLOCK_CHARS]}"
    return json.dumps(block, ensure_ascii=False)[:_MAX_BLOCK_CHARS]


def _render_message(msg: dict[str, Any]) -> str:
    role = msg.get("role", "?")
    content = msg.get("content", "")
    if isinstance(content, list):
        body = "\n".join(_render_block(b) for b in content if b is not None)
    else:
        body = _render_block(content)
    return f"### {role}\n{body.strip()}"


def serialize_transcript(messages: list[dict[str, Any]]) -> str:
    """Render the research agent's history as a single readable transcript.

    If it would exceed :data:`_MAX_TRANSCRIPT_CHARS`, keep the most recent turns
    and prepend an elision marker — newest context matters most for Q&A.
    """
    rendered = [_render_message(m) for m in messages]
    text = "\n\n".join(rendered)
    if len(text) <= _MAX_TRANSCRIPT_CHARS:
        return text
    # Keep the tail (most recent), drop from the front.
    kept: list[str] = []
    total = 0
    for chunk in reversed(rendered):
        total += len(chunk) + 2
        if total > _MAX_TRANSCRIPT_CHARS:
            break
        kept.append(chunk)
    kept.reverse()
    return "[… earlier turns elided to fit …]\n\n" + "\n\n".join(kept)


# ── read_research_state tool (live freshness on demand) ──────────────────────


class ResearchStateTool(Tool):
    """Read the CURRENT research state: idea-tree summary, recent reasoning/tool
    activity, and any messages the agent produced *since* this companion's
    snapshot. Use when the user asks what the agent is doing now."""

    name = "read_research_state"
    description = (
        "Read the CURRENT state of the running research agent: a summary of its "
        "idea tree (nodes, scores, best), its recent reasoning and tool activity, "
        "and any new messages since the conversation snapshot you were given. "
        "Call this whenever the user asks what the agent is doing *now* or about "
        "progress after the point where your snapshot ends."
    )
    input_schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    is_read_only = True

    def __init__(self, *, cwd: str, workspace_dir: str | None = None,
                 tree_path: Path, events_path: Path, messages_path: Path,
                 seed_message_count: int) -> None:
        super().__init__(cwd=cwd, workspace_dir=workspace_dir)
        self._tree_path = Path(tree_path)
        self._events_path = Path(events_path)
        self._messages_path = Path(messages_path)
        self._seed_count = seed_message_count

    async def execute(self, **kwargs: Any) -> str:
        return "\n\n".join([
            self._tree_summary(),
            self._recent_activity(),
            self._messages_delta(),
        ])

    def _tree_summary(self) -> str:
        try:
            data = json.loads(self._tree_path.read_text(encoding="utf-8"))
        except Exception:
            return "## idea tree\n(not available yet)"
        nodes = data.get("nodes") if isinstance(data, dict) else data
        if isinstance(nodes, dict):
            nodes = list(nodes.values())
        if not isinstance(nodes, list):
            return "## idea tree\n(empty)"
        lines = [f"## idea tree ({len(nodes)} nodes)"]
        for n in nodes[:30]:
            if not isinstance(n, dict):
                continue
            nid = n.get("id") or n.get("node_id") or "?"
            status = n.get("status", "?")
            score = n.get("score")
            hyp = (n.get("hypothesis") or n.get("idea") or "").replace("\n", " ")
            lines.append(f"- {nid} [{status}] score={score}  {hyp[:100]}")
        return "\n".join(lines)

    def _recent_activity(self) -> str:
        lines = self._tail_events(40)
        if not lines:
            return "## recent activity\n(none yet)"
        return "## recent activity\n" + "\n".join(lines)

    def _tail_events(self, n: int) -> list[str]:
        try:
            with self._events_path.open("r", encoding="utf-8") as fp:
                raw = list(deque(fp, maxlen=n * 3))  # stream the tail; never load the whole file
        except Exception:
            return []
        out: list[str] = []
        for line in raw:
            try:
                e = json.loads(line)
            except Exception:
                continue
            t = e.get("type", "")
            d = e.get("data", {})
            if t == "llm.thinking_delta":
                out.append(f"  think[{d.get('agent')}]: {str(d.get('text',''))[:120]}")
            elif t == "tool.start":
                out.append(f"  tool[{d.get('agent')}] {d.get('name')} {str(d.get('args_preview',''))[:80]}")
            elif t.startswith("idea."):
                out.append(f"  {t}: {d.get('node_id','')} {d.get('hypothesis','')}")
        return out[-n:]

    def _messages_delta(self) -> str:
        try:
            msgs = read_messages(self._messages_path)
        except Exception:
            msgs = []
        new = msgs[self._seed_count:]
        if not new:
            return "## new since your snapshot\n(no new agent messages)"
        return "## new since your snapshot\n" + serialize_transcript(new)[:8000]


_SYSTEM_PROMPT = """You are a read-only research companion embedded in a live \
terminal dashboard.

A long-running research agent is exploring ideas to optimize a metric. The user \
is watching it and wants to ask you questions about what it is doing and why — \
WITHOUT interrupting it. You are a SEPARATE agent: you cannot change the \
research, run experiments, or talk to the research agent.

Grounding:
- The conversation below is a snapshot of the research agent's full history \
(its reasoning, tool calls, and results) at the moment this session opened. \
Treat it as data to answer questions about.
- It may be stale — the agent keeps working. When the user asks about the \
CURRENT state or progress, call `read_research_state` to pull the live idea \
tree, recent activity, and any new messages.
- You also have read-only file tools (Read/Grep/Glob) to inspect the project. \
Note: files reflect the agent's CURRENT in-flight experiment branch.

Answer concisely and concretely, grounded in the snapshot and tools. If asked \
to change the run, explain that the user must use /steer for that — you cannot.\
"""


_GATE_SYSTEM_PROMPT = """You are an isolated gate discussion companion embedded \
in a live terminal dashboard.

The coordinator is PAUSED on a human decision gate. Your job is to help the \
user think through that gate: critique the proposed idea, compare alternatives, \
rewrite the hypothesis, or clarify what each option means. You are a SEPARATE, \
read-only agent: do not modify files, run experiments, or talk to the \
coordinator.

Important boundary:
- You CAN submit the final answer yourself once the user's intent is clear.
- Do not ask the user to learn or type command syntax.
- If the user is still exploring, ask a concise follow-up or propose a sharper \
rewrite. Do NOT submit yet.
- If the user clearly approves, rejects, chooses a direction, or gives a \
revision, submit exactly one final value by ending your response with this \
machine-readable marker on its own line:
    [[GATE_FINAL: <value>]]
- The final value must be exactly what the coordinator should receive: \
`approve`, `skip`, a free-form direction, or `edit <revised hypothesis>` for a \
rewritten idea.
- The marker is for the dashboard parser, not the user. Keep the visible answer \
short and natural.

Ground answers in the research transcript, current idea tree, recent events, \
and the gate prompt. Keep answers concise and practical.\
"""

_GATE_FINAL_RE = re.compile(r"\[\[GATE_FINAL:\s*(.*?)\s*\]\]", re.DOTALL)


# ── companion runtime ────────────────────────────────────────────────────────


class Companion:
    """Owns the read-only Q&A agent and runs it on a private event loop.

    Lazily builds the agent on the first question (seeding it from
    ``messages.jsonl``), then answers further questions in the same thread,
    serialised so they never overlap. Answers are pushed to ``RunState`` for the
    conversation panel to render.
    """

    def __init__(self, *, provider: LLMProvider, model: str, agent_cwd: str,
                 workspace_dir: str | None, run_state: RunState,
                 messages_path: Path, tree_path: Path, events_path: Path,
                 gate_submit: Callable[[dict[str, Any], str], None] | None = None) -> None:
        self._provider = provider
        self._model = model
        self._cwd = agent_cwd
        self._workspace_dir = workspace_dir
        self._state = run_state
        self._messages_path = Path(messages_path)
        self._tree_path = Path(tree_path)
        self._events_path = Path(events_path)
        self._gate_submit = gate_submit

        self._agent: Agent | None = None
        self._gate_agent: Agent | None = None
        self._gate_key: str | None = None
        self._lock: asyncio.Lock | None = None
        self._gate_lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()
        self._closed = False

    # ── public api (called from the dashboard's stdin thread) ──

    def ask(self, question: str) -> None:
        """Non-blocking: answer ``question`` on the companion loop; the result is
        pushed to ``RunState.companion_reply`` for the panel."""
        question = question.strip()
        if not question or self._closed:
            return
        self._state.companion_ask(question)
        loop = self._ensure_loop()
        asyncio.run_coroutine_threadsafe(self._answer(question), loop)

    def ask_gate(self, gate: dict[str, Any], question: str) -> None:
        """Non-blocking: discuss a pending gate without touching the run."""
        question = question.strip()
        if not question or self._closed:
            return
        self._state.gate_discussion_ask(question)
        loop = self._ensure_loop()
        asyncio.run_coroutine_threadsafe(self._answer_gate(dict(gate), question), loop)

    def close(self) -> None:
        self._closed = True
        loop, thread = self._loop, self._thread
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=2.0)

    # ── internals ──

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._thread_lock:
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                self._thread = threading.Thread(
                    target=self._loop.run_forever,
                    name="companion-loop",
                    daemon=True,
                )
                self._thread.start()
            return self._loop

    async def _answer(self, question: str) -> None:
        # Lazily create the Lock on first use: it runs on (and binds to) the
        # companion loop. Safe on a single loop — the check+create has no await
        # between it and assignment, so two queued answers can't both create one.
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:  # serialise questions — one at a time
            try:
                agent = self._build_agent_if_needed()
                reply = await agent.run(question)
            except Exception as exc:  # never crash the dashboard
                reply = f"⚠ companion error: {exc!r}"
            self._state.companion_reply(reply)

    async def _answer_gate(self, gate: dict[str, Any], question: str) -> None:
        if self._gate_lock is None:
            self._gate_lock = asyncio.Lock()
        async with self._gate_lock:
            try:
                agent = self._build_gate_agent_if_needed(gate)
                reply = await agent.run(question)
            except Exception as exc:  # never crash the dashboard
                reply = f"⚠ gate companion error: {exc!r}"
            visible_reply, final_value = _extract_gate_final(reply)
            if final_value is not None and not visible_reply:
                visible_reply = f"Submitted final feedback to the coordinator: {final_value}"
            self._state.gate_discussion_reply(visible_reply)
            if final_value is not None and self._gate_submit is not None:
                self._gate_submit(gate, final_value)

    def _build_agent_if_needed(self) -> Agent:
        if self._agent is not None:
            return self._agent

        seed_messages = seal_interrupted_tail(read_messages(self._messages_path))
        transcript = serialize_transcript(seed_messages) if seed_messages else \
            "(the research agent has not produced any messages yet)"

        # Keep the companion's own artifacts (ExperimentTracker dir, persisted
        # tool results) out of the target repo and the main session dir.
        comp_ws = str(Path(self._workspace_dir) / ".companion") if self._workspace_dir else None

        tools: list[Tool] = [
            FileReadTool(cwd=self._cwd, workspace_dir=comp_ws),
            GlobTool(cwd=self._cwd, workspace_dir=comp_ws),
            GrepTool(cwd=self._cwd, workspace_dir=comp_ws),
            ResearchStateTool(
                cwd=self._cwd, workspace_dir=comp_ws,
                tree_path=self._tree_path, events_path=self._events_path,
                messages_path=self._messages_path,
                seed_message_count=len(seed_messages),
            ),
        ]
        config = AgentConfig(
            cwd=self._cwd,
            model=self._model,
            max_turns=12,
            auto_git=False,      # read-only: never commit
            track_stats=False,   # never pollute the main run's stats (#13)
            event_bus=None,      # NullBus: never touch the shared event stream
            workspace_dir=comp_ws,
            agent_label="companion",
        )
        agent = Agent(
            provider=self._provider,
            tools=tools,
            system_prompt=_SYSTEM_PROMPT,
            config=config,
        )
        # Seed the transcript as DATA via a framing exchange, so the companion
        # treats it as the agent's history (not its own turns to continue).
        agent.messages = [
            {"role": "user", "content":
                "Here is the research agent's conversation so far (read-only, "
                "for grounding):\n\n" + transcript},
            {"role": "assistant", "content":
                "Understood — I have the research agent's history. "
                "Ask me anything about what it's doing or why."},
        ]
        self._agent = agent
        return agent

    def _build_gate_agent_if_needed(self, gate: dict[str, Any]) -> Agent:
        gate_key = json.dumps(gate, sort_keys=True, ensure_ascii=False, default=str)
        if self._gate_agent is not None and self._gate_key == gate_key:
            return self._gate_agent

        seed_messages = seal_interrupted_tail(read_messages(self._messages_path))
        transcript = serialize_transcript(seed_messages) if seed_messages else \
            "(the research agent has not produced any messages yet)"
        gate_context = _render_gate_context(gate)

        gate_ws = str(Path(self._workspace_dir) / ".gate_companion") if self._workspace_dir else None
        tools: list[Tool] = [
            FileReadTool(cwd=self._cwd, workspace_dir=gate_ws),
            GlobTool(cwd=self._cwd, workspace_dir=gate_ws),
            GrepTool(cwd=self._cwd, workspace_dir=gate_ws),
            ResearchStateTool(
                cwd=self._cwd, workspace_dir=gate_ws,
                tree_path=self._tree_path, events_path=self._events_path,
                messages_path=self._messages_path,
                seed_message_count=len(seed_messages),
            ),
        ]
        config = AgentConfig(
            cwd=self._cwd,
            model=self._model,
            max_turns=12,
            auto_git=False,
            track_stats=False,
            event_bus=None,
            workspace_dir=gate_ws,
            agent_label="gate-companion",
        )
        agent = Agent(
            provider=self._provider,
            tools=tools,
            system_prompt=_GATE_SYSTEM_PROMPT,
            config=config,
        )
        agent.messages = [
            {"role": "user", "content":
                "Here is the research agent's conversation so far (read-only, "
                "for grounding):\n\n" + transcript + "\n\n" + gate_context},
            {"role": "assistant", "content":
                "Understood — I can help discuss this paused gate. I will not "
                "modify files or run experiments. When the user's intent is clear, "
                "I will submit one final gate value using the GATE_FINAL marker."},
        ]
        self._gate_agent = agent
        self._gate_key = gate_key
        return agent


def _render_gate_context(gate: dict[str, Any]) -> str:
    options = gate.get("options") or []
    return "\n".join([
        "## Current Paused Gate",
        f"kind: {gate.get('kind') or 'unknown'}",
        f"node_id: {gate.get('node_id') or ''}",
        "prompt:",
        str(gate.get("prompt") or ""),
        "options: " + (", ".join(str(o) for o in options) if options else "free-form"),
    ])


def _extract_gate_final(reply: str) -> tuple[str, str | None]:
    """Return (visible_reply, final_value) from a gate companion response."""
    text = (reply or "").strip()
    match = _GATE_FINAL_RE.search(text)
    if match is None:
        return text, None
    final_value = " ".join(match.group(1).split()).strip()
    visible = _GATE_FINAL_RE.sub("", text).strip()
    return visible, final_value or None
