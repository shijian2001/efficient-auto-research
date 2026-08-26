"""Shared mutable state for the live run dashboard.

The Rich Live view, the event-bus subscriber, the DISPLAY_HOOK adapter,
and the stdin reader all funnel into a single ``RunState`` instance so
the rendered UI is just a pure function of state. That keeps the
display loop simple: any mutation triggers one redraw.

Two facts to keep in mind:
 - Mutations may arrive from threads (the stdin reader runs in its own
   thread) and from the event loop (bus + tool hook). Updates are
   small dict/deque writes; we guard the few non-atomic transitions
   with ``self._lock``.
 - We never store the giant raw payloads (full tool outputs, assistant
   reasoning text). Those live in events.jsonl / REPORT.md. The state
   only keeps what the UI needs.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


# Tool name → arbor cycle phase. Same table as the previous RunDisplay.
TOOL_PHASE: dict[str, str] = {
    "TreeView":                  "observe",
    "SearchIdeaContext":         "observe",
    "SearchIdeaContextParallel": "observe",
    "SearchStatus":              "observe",
    "TreeAddNode":               "ideate",
    "RunExecutor":               "dispatch",
    "RunExecutorParallel":       "dispatch",
    "TreeUpdateNode":            "backprop",
    "TreePropagate":             "backprop",
    "TreePrune":                 "decide",
    "TreeSetMeta":               "decide",
    "GitMergeBranch":            "decide",
}


@dataclass
class IdeaRecord:
    """One row in the explored-ideas table — the dashboard's main
    information surface.

    The status semantics intentionally follow the dashboard.py HTML
    report so users see the same status words in both places:

        proposed → running → done → merged           (happy path)
        proposed → running → done                    (kept but not yet merged)
        proposed → running → failed                  (executor crashed)
        proposed → pruned                            (no longer worth exploring)
    """
    node_id: str
    hypothesis: str
    status: str = "proposed"          # see above
    score: float | None = None
    branch: str | None = None
    parent_id: str | None = None      # for rendering the tree view
    proposed_at: float = field(default_factory=time.monotonic)
    started_at: float | None = None   # set when executor launches
    finished_at: float | None = None
    pruned_reason: str | None = None
    # Optional one-line takeaway from exploring this idea (what was learned).
    # Forward-looking: surfaced in the tree view when the pipeline fills it.
    insight: str | None = None


@dataclass
class RunState:
    """Everything the live dashboard renders + the user-message queue."""

    # ── identity / context (set once at launch) ──
    run_name: str = ""
    task: str = ""
    cwd: str = ""
    model: str = ""
    session_dir: str = ""
    webui_url: str = ""

    # ── progress ──
    started_at: float = field(default_factory=time.monotonic)
    cycle_num: int | None = None
    total_cycles: int | None = None
    phase: str | None = None

    # ── what the agent is working on right now ──
    # Set when the coordinator calls TreeAddNode / RunExecutor / etc.
    # so the dashboard can name the idea it's currently exploring.
    current_idea_node: str | None = None
    current_idea_hypothesis: str | None = None

    # Single-line "now" action — overwritten on every tool call so it
    # acts as an in-place status spinner, not a growing log.
    now_action: str = ""              # rich-markup
    now_action_started_at: float = 0.0

    # ── explored-ideas ledger (replaces the noisy event stream) ──
    # ``ideas`` is keyed by node_id; ``idea_order`` records insertion
    # order so the dashboard can render newest-first without sorting
    # on every paint.
    ideas: dict[str, IdeaRecord] = field(default_factory=dict)
    idea_order: list[str] = field(default_factory=list)

    # ── idea counters (cached for the header) ──
    ideas_proposed: int = 0
    ideas_done: int = 0
    ideas_pruned: int = 0
    ideas_merged: int = 0
    ideas_running: int = 0
    ideas_needs_retry: int = 0
    best_score: float | None = None
    metric_direction: str = "maximize"
    baseline_score: float | None = None
    trunk_score: float | None = None
    # Append-only history of best_score whenever it changes — drives
    # the sparkline rendered in the dashboard header.
    best_score_history: list[float] = field(default_factory=list)
    # Whether the live progress chart panel is shown (toggled by /chart).
    show_chart: bool = True
    # Cumulative token usage observed via LLM_CALL events; surfaced as
    # "tokens / cost" in the header so the user can see budget burn.
    tokens_in: int = 0
    tokens_out: int = 0
    # Cumulative KV-cache accounting (#13), observed via LLM_CALL payloads.
    # Drives the cache hit-rate shown in the WebUI / header.
    cache_read_total: int = 0
    cache_creation_total: int = 0
    uncached_total: int = 0

    # ── transient error flash for the header ──
    # When an LLM error / tool failure happens, surface it briefly so
    # the user notices something needs attention without it sticking
    # around forever.
    last_error_text: str | None = None
    last_error_at: float = 0.0
    last_error_style: str = "red"
    last_error_glyph: str = "!"

    # ── interactive Q & A ──
    # Each message the user types is appended to ``_pending_user_messages``
    # and the agent loop drains them between turns. The latest assistant
    # text we see *after* a user note is shown in the reply panel.
    _pending_user_messages: list[str] = field(default_factory=list)
    _pending_control_messages: list[str] = field(default_factory=list)
    last_user_message: str | None = None
    last_user_message_at: float = 0.0
    last_assistant: str | None = None
    last_assistant_at: float = 0.0
    awaiting_reply: bool = False
    # Rolling micro-activity feed for the reply panel — populated by
    # on_tool_call while the agent is busy answering the user. Keeps
    # the "thinking…" line from looking frozen.
    reply_activity: deque[str] = field(
        default_factory=lambda: deque(maxlen=4)
    )
    # Detected user language (e.g. "zh", "en", "ja"). Set from the first
    # message the user types and injected into the agent's prompt so
    # replies match the user's language.
    user_locale: str | None = None
    # Optional durable transcript for interactive questions asked during
    # the run. The live panel is intentionally compact; this file keeps
    # the complete user-visible conversation.
    conversation_path: str | None = None
    paused: bool = False

    # ── read-only Q&A companion (#11) ─────────────────────────────────
    # The conversation panel talks to a separate, read-only companion agent
    # (see cli/companion.py), NOT the research agent — so questions never
    # touch the main loop. These fields drive that panel; they are distinct
    # from the main agent's last_assistant/awaiting_reply (used by /steer).
    companion_turns: deque[tuple[str, str]] = field(
        default_factory=lambda: deque(maxlen=8)
    )
    companion_busy: bool = False
    # Wall-clock when the companion started preparing the current reply. Drives
    # the "thinking… <elapsed>" liveness readout so a slow answer never looks
    # frozen. 0.0 when idle.
    companion_busy_since: float = 0.0
    # Monotonically increasing count of completed companion answers. The
    # dashboard compares against it to mirror each new answer to the scrollback
    # exactly once (robust even though companion_turns is a bounded deque).
    companion_reply_seq: int = 0
    # The conversation panel can expand to claim most of the screen so a long
    # answer is readable inline (Tab / /reply toggles it). reply_scroll is the
    # first body line shown while expanded (PgUp/PgDn/arrows move it).
    reply_expanded: bool = False
    reply_scroll: int = 0

    # ── slash-command output (#shown inline above the input) ──────────
    # The result of a dashboard command (/cost, /status, …). Rendered as a
    # transient panel just above the input box instead of being printed to the
    # scrollback (which, under the full-screen pt app, scrolls off the top).
    command_output: tuple[str, list[str]] | None = None
    command_output_at: float = 0.0

    # ── HITL review gate (#2) — the engine is blocked awaiting a human ──
    # Set from AWAIT_USER ({kind, prompt, node_id, options}), cleared on the
    # matching USER_INPUT_RECEIVED. Drives the gate panel + input routing.
    pending_gate: dict[str, Any] | None = None
    # While a gate is open, user text starts a side discussion with an isolated
    # read-only companion. Only an explicit /accept-like command is delivered
    # back to the coordinator as USER_INPUT_RECEIVED.
    gate_discussion_turns: deque[tuple[str, str]] = field(
        default_factory=lambda: deque(maxlen=8)
    )
    gate_discussion_busy: bool = False
    gate_discussion_busy_since: float = 0.0

    # ── live reasoning (#6) — streamed thinking + per-agent tool activity ──
    # Fed by the bus events THINKING_DELTA / TOOL_START / TOOL_END / HEARTBEAT
    # (attributed by agent label + node_id), rendered in the reasoning panel.
    # Purely additive: independent of the legacy DISPLAY_HOOK / now_action path.
    thinking_feed: deque[tuple[str, str]] = field(
        default_factory=lambda: deque(maxlen=10)
    )
    # agent label -> current tool: {"tool", "node_id", "started_at", "ok"}.
    # ok is None while running, True/False once the tool finished.
    agent_activity: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Wall-clock of the most recent reasoning/tool/heartbeat signal — drives a
    # "working" liveness hint so a long LLM call doesn't look frozen.
    last_activity_at: float = 0.0

    # ── plumbing ──
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _dirty: threading.Event = field(default_factory=threading.Event)

    # ── mutation api ─────────────────────────────────────────────

    def mark_dirty(self) -> None:
        """Signal the Live loop that a redraw is needed."""
        self._dirty.set()

    def set_phase(self, phase: str) -> None:
        if phase == self.phase:
            return
        self.phase = phase
        self.mark_dirty()

    def set_now(self, text: str) -> None:
        """Overwrite the single 'now' line. Acts as an in-place status
        spinner — the previous value is discarded, not buffered."""
        self.now_action = text
        self.now_action_started_at = time.monotonic()
        self.mark_dirty()

    def set_webui_url(self, url: str | None) -> None:
        self.webui_url = (url or "").strip()
        self.mark_dirty()

    def push_user_message(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        with self._lock:
            self._pending_user_messages.append(text)
        self.last_user_message = text
        self.last_user_message_at = time.monotonic()
        self.awaiting_reply = True
        self.reply_activity.clear()
        # Detect language from the very first user message and keep it
        # sticky for the rest of the session. We pass it to the agent
        # via the inter-turn note prefix so replies match the user.
        if self.user_locale is None:
            self.user_locale = _detect_locale(text)
        self._append_conversation("User", text)
        # The conversation panel already surfaces this; no need to
        # push a duplicate "you: ..." row anywhere else.
        self.mark_dirty()

    def push_control_message(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        with self._lock:
            self._pending_control_messages.append(text)
        self.mark_dirty()

    # ── read-only companion (#11) ─────────────────────────────────────

    def set_command_output(self, title: str, rows: list[str]) -> None:
        """Stash a dashboard command's output to render inline above the input."""
        self.command_output = (title, list(rows))
        self.command_output_at = time.monotonic()
        self.mark_dirty()

    def companion_ask(self, text: str) -> None:
        """Record a user question routed to the read-only companion."""
        text = text.strip()
        if not text:
            return
        self.companion_turns.append(("you", text))
        self.companion_busy = True
        self.companion_busy_since = time.monotonic()
        if self.user_locale is None:
            self.user_locale = _detect_locale(text)
        self._append_conversation("User", text)
        self.mark_dirty()

    def companion_reply(self, text: str) -> None:
        """Record the companion's answer (or an error note)."""
        text = (text or "").strip()
        if text:
            # Append the turn before clearing busy, so the paint loop never
            # observes "not busy and no reply yet".
            self.companion_turns.append(("companion", text))
            self._append_conversation("Companion", text)
            self.companion_reply_seq += 1
            self.reply_scroll = 0          # show a freshly-arrived answer from the top
        self.companion_busy = False
        self.companion_busy_since = 0.0
        self.mark_dirty()

    # ── isolated gate discussion ─────────────────────────────────────

    def gate_discussion_ask(self, text: str) -> None:
        """Record a user turn in the current gate's isolated discussion."""
        text = text.strip()
        if not text:
            return
        self.gate_discussion_turns.append(("you", text))
        self.gate_discussion_busy = True
        self.gate_discussion_busy_since = time.monotonic()
        if self.user_locale is None:
            self.user_locale = _detect_locale(text)
        self._append_conversation("Gate User", text)
        self.mark_dirty()

    def gate_discussion_reply(self, text: str) -> None:
        """Record the gate companion's answer (or an error note)."""
        text = (text or "").strip()
        if text:
            self.gate_discussion_turns.append(("companion", text))
            self._append_conversation("Gate Companion", text)
            self.reply_scroll = 0
        self.gate_discussion_busy = False
        self.gate_discussion_busy_since = 0.0
        self.mark_dirty()

    # ── HITL review gate (#2) ─────────────────────────────────────────

    def open_gate(self, e: Any) -> None:
        """The engine is blocked awaiting a human decision (AWAIT_USER)."""
        self.pending_gate = dict(getattr(e, "data", None) or {})
        self.gate_discussion_turns.clear()
        self.gate_discussion_busy = False
        self.gate_discussion_busy_since = 0.0
        self.mark_dirty()

    def close_gate(self, _e: Any = None) -> None:
        """The decision was delivered (USER_INPUT_RECEIVED) — drop the gate."""
        if self.pending_gate is not None:
            self.pending_gate = None
            self.gate_discussion_busy = False
            self.gate_discussion_busy_since = 0.0
            self.mark_dirty()

    def on_assistant_text(self, text: str) -> None:
        """Capture the coordinator's text output.

        Most assistant text is internal reasoning from the research loop
        — we don't surface it. Only when the user just asked a question
        (``awaiting_reply`` is True) do we treat the next assistant text
        as a reply and store it for the conversation panel.
        """
        if not text or not text.strip():
            return
        if not self.awaiting_reply:
            return
        self.last_assistant = text.strip()
        self.last_assistant_at = time.monotonic()
        self.awaiting_reply = False
        self.reply_activity.clear()
        self._append_conversation("Agent", self.last_assistant)
        self.mark_dirty()

    def _append_conversation(self, speaker: str, text: str) -> None:
        """Append a complete post-launch Q&A turn to disk, if enabled."""
        if not self.conversation_path or not text.strip():
            return
        try:
            path = Path(self.conversation_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with path.open("a", encoding="utf-8") as f:
                f.write(f"\n\n## {speaker} · {stamp}\n\n")
                f.write(text.strip())
                f.write("\n")
        except OSError:
            return

    def drain_user_messages(self) -> list[str]:
        with self._lock:
            msgs, self._pending_user_messages = self._pending_user_messages, []
            control, self._pending_control_messages = self._pending_control_messages, []
        # Append a compact locale hint to each user message so the
        # agent answers in the same language the user typed in. We
        # only annotate once we've detected a locale.
        if msgs and self.user_locale and self.user_locale != "und":
            tag = f"  (system: reply to the user in {self.user_locale})"
            msgs = [m + tag for m in msgs]
        return control + msgs

    # ── event subscribers ────────────────────────────────────────

    def on_session_start(self, e: Any) -> None:
        # No activity row — the dashboard's chrome already announces
        # the run. Header counters reset themselves on construction.
        self.mark_dirty()

    def on_session_end(self, e: Any) -> None:
        # Same: rendering the final state is enough.
        self.mark_dirty()

    def on_cycle_start(self, e: Any) -> None:
        n = e.data.get("cycle_num")
        total = e.data.get("total_cycles")
        if isinstance(n, int):
            self.cycle_num = n
        if isinstance(total, int):
            self.total_cycles = total
        self.mark_dirty()

    def on_cycle_end(self, e: Any) -> None:
        # cycle_num already updated when the *next* cycle starts; no
        # extra row needed.
        self.mark_dirty()

    def on_phase_change(self, e: Any) -> None:
        phase = e.data.get("phase")
        if isinstance(phase, str):
            self.set_phase(phase)

    # ── idea-ledger updates ─────────────────────────────────────

    def _upsert(self, node_id: str) -> IdeaRecord:
        rec = self.ideas.get(node_id)
        if rec is None:
            rec = IdeaRecord(node_id=node_id, hypothesis="")
            self.ideas[node_id] = rec
            self.idea_order.append(node_id)
        return rec

    def on_idea_proposed(self, e: Any) -> None:
        d = e.data
        node = d.get("node_id")
        if not isinstance(node, str):
            return
        hyp = (d.get("hypothesis") or "").strip().replace("\n", " ")
        parent = d.get("parent_id")
        rec = self._upsert(node)
        rec.hypothesis = hyp
        rec.status = "proposed"
        if isinstance(parent, str) and parent:
            rec.parent_id = parent
        self.ideas_proposed = len(self.ideas)
        self.current_idea_node = node
        self.current_idea_hypothesis = hyp
        self.mark_dirty()

    def _bump_best(self, score: float) -> None:
        """Update best_score and append to the history sparkline only
        when the score actually improved. Keeps the sparkline a real
        signal rather than noise from every completed idea."""
        score = float(score)
        if self._is_better_score(score, self.best_score):
            self.best_score = score
            self.best_score_history.append(score)
            # Cap history so paint stays cheap on long runs.
            if len(self.best_score_history) > 64:
                del self.best_score_history[: len(self.best_score_history) - 64]

    def _is_better_score(self, candidate: float, incumbent: float | None) -> bool:
        if incumbent is None:
            return True
        if self.metric_direction == "minimize":
            return candidate < incumbent
        return candidate > incumbent

    def _recompute_best_score(self) -> None:
        scored = [
            rec.score for rec in self.ideas.values()
            if rec.status in ("done", "merged") and rec.score is not None
        ]
        if not scored:
            self.best_score = None
            self.best_score_history.clear()
            return
        self.best_score = min(scored) if self.metric_direction == "minimize" else max(scored)
        self.best_score_history = [self.best_score]

    def update_tree_meta(self, inputs: dict[str, Any]) -> None:
        """Mirror user-visible tree metadata discovered by TreeSetMeta."""
        changed = False
        direction = inputs.get("metric_direction")
        if isinstance(direction, str) and direction.lower() in ("maximize", "minimize"):
            normalized = direction.lower()
            if normalized != self.metric_direction:
                self.metric_direction = normalized
                self._recompute_best_score()
                changed = True
        for attr, key in (("baseline_score", "baseline_score"), ("trunk_score", "trunk_score")):
            value = inputs.get(key)
            if isinstance(value, (int, float)):
                setattr(self, attr, float(value))
                changed = True
        if changed:
            self.mark_dirty()

    def on_llm_call(self, e: Any) -> None:
        d = e.data
        self.tokens_in += int(d.get("input_tokens") or 0)
        self.tokens_out += int(d.get("output_tokens") or 0)
        self.cache_read_total += int(d.get("cache_read_tokens") or 0)
        self.cache_creation_total += int(d.get("cache_creation_tokens") or 0)
        self.uncached_total += int(d.get("uncached_input_tokens") or 0)
        self.mark_dirty()

    @property
    def cache_hit_rate(self) -> float:
        """Fraction of logical input tokens served from cache (0.0–1.0)."""
        total = self.cache_read_total + self.cache_creation_total + self.uncached_total
        return (self.cache_read_total / total) if total else 0.0

    def on_idea_completed(self, e: Any) -> None:
        d = e.data
        node = d.get("node_id")
        if not isinstance(node, str):
            return
        rec = self._upsert(node)
        score = d.get("score")
        if isinstance(score, (int, float)):
            rec.score = float(score)
            self._bump_best(score)
        status = (d.get("status") or "done").lower()
        rec.status = status if status in ("done", "failed", "needs_retry") else "done"
        rec.finished_at = time.monotonic()
        self._recount()
        self.mark_dirty()

    def on_idea_pruned(self, e: Any) -> None:
        d = e.data
        node = d.get("node_id")
        if not isinstance(node, str):
            return
        rec = self._upsert(node)
        rec.status = "pruned"
        rec.pruned_reason = (d.get("reason") or "").strip() or None
        rec.finished_at = time.monotonic()
        self._recount()
        self.mark_dirty()

    def on_idea_merged(self, e: Any) -> None:
        d = e.data
        node = d.get("node_id")
        if not isinstance(node, str):
            return
        rec = self._upsert(node)
        rec.status = "merged"
        to_score = d.get("to_score")
        if isinstance(to_score, (int, float)):
            rec.score = float(to_score)
            self._bump_best(to_score)
        rec.finished_at = time.monotonic()
        self._recount()
        self.mark_dirty()

    def on_executor_start(self, e: Any) -> None:
        d = e.data
        node = d.get("node_id")
        if not isinstance(node, str):
            return
        rec = self._upsert(node)
        rec.status = "running"
        rec.branch = d.get("branch") or rec.branch
        rec.started_at = time.monotonic()
        self.current_idea_node = node
        if rec.hypothesis:
            self.current_idea_hypothesis = rec.hypothesis
        self._recount()
        branch_suffix = f"  [dim]on[/] [cyan]{rec.branch}[/]" if rec.branch else ""
        self.set_now(f"[magenta]running executor {node}[/]{branch_suffix}")

    def on_executor_end(self, e: Any) -> None:
        d = e.data
        node = d.get("node_id")
        if not isinstance(node, str):
            return
        rec = self._upsert(node)
        # Executor end doesn't tell us merged-vs-done yet; the orchestrator
        # decides that and fires IDEA_COMPLETED / IDEA_MERGED / IDEA_PRUNED
        # next. We just stop the runtime clock here.
        score = d.get("score")
        if isinstance(score, (int, float)):
            rec.score = float(score)
            self._bump_best(score)
        if rec.status == "running":
            # Provisional until the completed event lands. The executor reports
            # its classified outcome on EXECUTOR_END, so honour it when present
            # (avoids briefly showing a needs_retry node as "done").
            ev_status = (d.get("status") or "").lower()
            rec.status = ev_status if ev_status in ("done", "needs_retry", "failed") else "done"
        rec.finished_at = time.monotonic()
        if self.current_idea_node == node:
            self.current_idea_node = None
        self._recount()
        self.mark_dirty()

    def on_llm_error(self, e: Any) -> None:
        d = e.data
        msg = str(d.get("error") or "")[:120]
        provider = d.get("provider")
        prefix = f"{provider}: " if provider else ""
        self.last_error_text = f"{prefix}{msg}"
        self.last_error_at = time.monotonic()
        self.last_error_style = "red"
        self.last_error_glyph = "!"
        self.mark_dirty()

    def on_convergence(self, e: Any) -> None:
        d = e.data
        final = d.get("final_score")
        if isinstance(final, (int, float)):
            self._bump_best(final)

    # ── live reasoning (#6): bus handlers for the reasoning panel ──
    # Attributed by agent label ("coordinator" | "sub:<node_id>" | "search") so the
    # panel can show which executor is reasoning/acting during parallel runs.

    def on_thinking_delta(self, e: Any) -> None:
        text = (e.data.get("text") or "").strip()
        if not text:
            return
        agent = e.data.get("agent") or "meta"
        self.thinking_feed.append((agent, _short(text, 200)))
        self.last_activity_at = time.monotonic()
        self.mark_dirty()

    def on_tool_start(self, e: Any) -> None:
        agent = e.data.get("agent") or "meta"
        self.agent_activity[agent] = {
            "tool": e.data.get("name") or "?",
            "node_id": e.data.get("node_id") or "",
            "preview": _short(e.data.get("args_preview") or "", 80),
            "started_at": time.monotonic(),
            "ok": None,
        }
        self._evict_finished_agents()
        self.last_activity_at = time.monotonic()
        self.mark_dirty()

    def _evict_finished_agents(self, keep: int = 24) -> None:
        """Bound ``agent_activity`` over long runs: drop the oldest *finished*
        entries once the map grows past ``keep`` (running ones are never evicted)."""
        if len(self.agent_activity) <= keep:
            return
        finished = [a for a, v in self.agent_activity.items() if v.get("ok") is not None]
        for a in finished[: len(self.agent_activity) - keep]:
            self.agent_activity.pop(a, None)

    def on_tool_end(self, e: Any) -> None:
        agent = e.data.get("agent") or "meta"
        entry = self.agent_activity.get(agent)
        if entry is not None:
            entry["ok"] = bool(e.data.get("ok", True))
            entry["duration"] = e.data.get("duration")
        self.last_activity_at = time.monotonic()
        self.mark_dirty()

    def on_heartbeat(self, e: Any) -> None:
        self.last_activity_at = time.monotonic()
        self.mark_dirty()

    def _recount(self) -> None:
        """Recompute the small idea counters from the ledger so the
        header stays consistent without us tracking deltas by hand."""
        c = {"running": 0, "done": 0, "merged": 0, "pruned": 0, "failed": 0, "needs_retry": 0}
        for rec in self.ideas.values():
            if rec.status in c:
                c[rec.status] += 1
        self.ideas_proposed = len(self.ideas)
        self.ideas_running = c["running"]
        self.ideas_done = c["done"] + c["merged"]   # both count toward "completed"
        self.ideas_pruned = c["pruned"] + c["failed"]
        self.ideas_merged = c["merged"]
        self.ideas_needs_retry = c["needs_retry"]   # incomplete — its own bucket

    def seed_from_tree(self, tree_data: dict[str, Any]) -> None:
        """Rehydrate the idea ledger from a persisted idea tree (for --resume).

        The header counters and tree view are event-driven, but resume loads the
        tree without replaying ``idea.*`` events — so without this the dashboard
        shows "branches 0/N" and an empty tree even for a run that already
        explored many branches. We populate records directly (not via the bus)
        so the persistent ``events.jsonl`` is never double-written.

        Mirrors live behaviour: the root/baseline node is seeded straight into
        the tree and never emits ``IDEA_PROPOSED``, so we skip it here too —
        otherwise the branch count would be off by one.
        """
        if not isinstance(tree_data, dict):
            return
        nodes = tree_data.get("nodes")
        if not isinstance(nodes, dict):
            return
        root_id = tree_data.get("root_id")
        meta = tree_data.get("meta")
        if isinstance(meta, dict):
            direction = meta.get("metric_direction")
            if isinstance(direction, str) and direction.lower() in ("maximize", "minimize"):
                self.metric_direction = direction.lower()
        for node_id, nd in nodes.items():
            if node_id == root_id or not isinstance(nd, dict):
                continue
            status = nd.get("status") or "proposed"
            if status == "pending":            # not-yet-run node → "proposed" in dashboard terms
                status = "proposed"
            rec = self._upsert(node_id)
            rec.hypothesis = (nd.get("hypothesis") or "").strip().replace("\n", " ")
            rec.status = status
            rec.score = nd.get("score")
            parent = nd.get("parent_id")
            rec.parent_id = parent if isinstance(parent, str) else None
            branch = nd.get("code_ref")
            rec.branch = branch if isinstance(branch, str) else None
        self._recount()
        self._recompute_best_score()
        self.mark_dirty()

    # ── DISPLAY_HOOK adapter ─────────────────────────────────────

    def on_tool_call(self, name: str, inputs: dict[str, Any]) -> None:
        """Hook target for ``core.agent.DISPLAY_HOOK``.

        Tool calls feed the in-place ``now`` line, NOT the milestones
        deque — they're too noisy to keep around. The phase header
        also updates here.

        While the user is awaiting a reply we also push a short label
        into ``reply_activity`` so the conversation panel shows the
        agent doing real work instead of a frozen "thinking…" line.
        """
        phase = TOOL_PHASE.get(name)
        if phase:
            self.set_phase(phase)
        if name == "TreeSetMeta":
            self.update_tree_meta(inputs)
        verb, key = _TOOL_LABEL.get(name, (name, None))
        target = _pick_target(inputs, key)
        suffix = f"  [bright_black]{target}[/]" if target else ""
        self.set_now(f"[dim]{verb}[/]{suffix}")
        if self.awaiting_reply:
            label = f"{verb}{(' · ' + target) if target else ''}"
            self.reply_activity.append(label[:80])
            self.mark_dirty()

    def on_tool_error(self, name: str, output: str) -> None:
        first = (output.splitlines() or [""])[0]
        self.last_error_text = f"{name} failed: {first[:100]}"
        self.last_error_at = time.monotonic()
        self.last_error_style = "red"
        self.last_error_glyph = "!"
        self.mark_dirty()

    def on_status_narration(self, msg: str, *, style: str, glyph: str) -> None:
        """Status messages routed through the dashboard header.

        Preserve the caller's style/glyph so normal green/cyan status does not
        look like an error just because it shares this compact header slot.
        """
        self.last_error_text = msg
        self.last_error_at = time.monotonic()
        self.last_error_style = style or "dim"
        self.last_error_glyph = glyph or "·"
        self.mark_dirty()

    # ── derived render helpers ───────────────────────────────────

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def branch_budget_used(self) -> int:
        # Must mirror _CYCLE_STATUSES (executor_run.py): done/merged/pruned/failed
        # AND needs_retry all consume a cycle toward max_cycles.
        return self.ideas_done + self.ideas_pruned + self.ideas_needs_retry


# ── module-level current state ─────────────────────────────────────
#
# A small global lets the orchestrator / cli.style helpers route status
# messages through the dashboard without having to be passed an
# explicit handle. Set by RunDashboard on enter, cleared on exit.

CURRENT: RunState | None = None


def set_current(state: RunState | None) -> None:
    global CURRENT
    CURRENT = state


# ── helpers (mirror run_display) ───────────────────────────────────


# Friendly verb + key-input picker for the in-place "now" line. Covers
# every coordinator tool so the user always sees a human-readable action,
# not the raw class name.
_TOOL_LABEL: dict[str, tuple[str, str | None]] = {
    "TreeView":                  ("inspecting the tree",       None),
    "TreeAddNode":               ("proposing new idea",        "hypothesis"),
    "TreeUpdateNode":            ("recording result",          "node_id"),
    "TreePrune":                 ("pruning idea",              "node_id"),
    "TreeSetMeta":               ("updating tree meta",        "key"),
    "TreePropagate":             ("propagating score",         "node_id"),
    "SearchIdeaContext":         ("searching literature",      "query"),
    "SearchIdeaContextParallel": ("searching literature ×N",   "queries"),
    "SearchStatus":              ("checking search status",    None),
    "RunExecutor":               ("launching executor",        "node_id"),
    "RunExecutorParallel":       ("launching executors ×N",    "nodes"),
    "GitMergeBranch":            ("merging branch",            "source_branch"),
}


def _pick_target(inputs: dict[str, Any], preferred_key: str | None) -> str:
    if preferred_key and inputs.get(preferred_key) is not None:
        return _short(_stringify(inputs[preferred_key]), 60)
    for v in inputs.values():
        if v:
            return _short(_stringify(v), 60)
    return ""


def _stringify(v: Any) -> str:
    if isinstance(v, (list, tuple)):
        return f"×{len(v)}"
    return str(v).strip()


def _short(s: str, limit: int) -> str:
    s = s.replace("\n", " ").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _detect_locale(text: str) -> str:
    """Cheap unicode-block heuristic for the user's primary language.

    Returns an ISO-639-1 (or close) code so the agent's reply matches
    what the user typed. We only inspect alphabetic characters — digits
    and punctuation aren't language-bearing — and pick the dominant
    script. Mixed input (CJK + ASCII) is common; the CJK side wins as
    soon as it crosses a 30% threshold so a sentence sprinkled with
    Latin code or model names still routes to Chinese / Japanese / Korean.

    The returned code is what we hand to the model in plain language,
    so we deliberately keep it human-readable (``"Chinese"`` rather
    than ``"zh-CN"``) — the LLM uses it as instruction, not a lookup.
    """
    chinese = japanese_kana = korean = arabic = cyrillic = latin = 0
    for c in text:
        if not c.isalpha() and not ("぀" <= c <= "ヿ"):
            continue
        cp = ord(c)
        if 0x4e00 <= cp <= 0x9fff:                           # CJK Unified
            chinese += 1
        elif 0x3040 <= cp <= 0x30ff:                         # Hiragana / Katakana
            japanese_kana += 1
        elif 0xac00 <= cp <= 0xd7af:                         # Hangul Syllables
            korean += 1
        elif 0x0600 <= cp <= 0x06ff:                         # Arabic
            arabic += 1
        elif 0x0400 <= cp <= 0x04ff:                         # Cyrillic
            cyrillic += 1
        elif (0x41 <= cp <= 0x5a) or (0x61 <= cp <= 0x7a):   # Latin
            latin += 1
    total = chinese + japanese_kana + korean + arabic + cyrillic + latin
    if total == 0:
        return "und"
    # Hangul / kana win immediately when present — they're unambiguous.
    if korean / total >= 0.20:
        return "Korean"
    if japanese_kana / total >= 0.20:
        return "Japanese"
    if chinese / total >= 0.30:
        # Could be either Chinese or Japanese-with-only-kanji. Without
        # kana we default to Chinese, which is by far the more common case.
        return "Chinese"
    if arabic / total >= 0.30:
        return "Arabic"
    if cyrillic / total >= 0.30:
        return "Russian"
    return "English"
