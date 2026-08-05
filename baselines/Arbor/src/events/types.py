"""Event type constants emitted on the EventBus.

Subscribers match on these strings. Keep names stable across versions —
external tooling (file_logger, dashboards) joins to them.
"""

# ── Session lifecycle ──
SESSION_START = "session.start"           # {task, cwd, provider, model}
SESSION_END = "session.end"               # {duration, exit_reason, turns, input_tokens, output_tokens, meta_input_tokens, meta_output_tokens}

# ── Arbor Cycle ──
CYCLE_START = "cycle.start"               # {cycle_num, total_cycles}
CYCLE_END = "cycle.end"                   # {cycle_num, duration}
PHASE_CHANGE = "cycle.phase"              # {phase}

# ── Idea Tree ──
IDEA_PROPOSED = "idea.proposed"           # {node_id, hypothesis, parent_id}
IDEA_COMPLETED = "idea.completed"         # {node_id, score, status}
IDEA_PRUNED = "idea.pruned"               # {node_id, reason}
IDEA_MERGED = "idea.merged"               # {node_id, from_score, to_score, branch}
TREE_UPDATED = "tree.updated"             # {tree_snapshot_path}

# ── Executor ──
EXECUTOR_START = "executor.start"         # {node_id, idea, branch}
EXECUTOR_END = "executor.end"             # {node_id, score, duration, tokens}

# ── LLM ──
LLM_CALL = "llm.call"                     # {provider, model, input_tokens, output_tokens, cache_*}
LLM_ERROR = "llm.error"                   # {provider, error, retrying}

# ── Evaluation ──
# NOT YET EMITTED: the consumer side is wired (stats_collector counts eval
# failures) but the engine does not emit this yet. Kept so the consumer stays
# live for when scoring is moved onto the bus.
EVAL_END = "eval.end"                     # {node_id, score, duration, error?}

# Emitted when a protected path was changed during a run (manifest mismatch);
# the node's dev score is invalidated and the branch becomes merge-ineligible.
PROTECTED_TAMPER = "eval.protected_tamper"   # {node_id, branch, changes}
# Emitted once at INIT with the contamination assessment for the benchmark.
CONTAMINATION_ASSESSED = "eval.contamination_assessed"  # {status, reasons}

# ── Convergence ──
# NOT YET EMITTED: cli_logger renders this, but no emitter exists yet. Kept so
# the renderer stays live for when convergence detection lands.
CONVERGENCE_REACHED = "convergence.reached"  # {reason, final_score}

# ── User Interaction ──
# Reply channel for the engine's structured AWAIT_USER prompt (below) and for
# the stdin-injection path. Both reply with {node_id, value}.
USER_INPUT_RECEIVED = "user.input_received"

# ── Contract 2 additions (D1 freeze) ───────────────────────────────────────
# Fine-grained / streaming events consumed by tree viz (#6), WebUI (#7),
# cache governance (#13) and HITL (#2/#10/#11). ADDED ONLY — never rename the
# constants above; external tooling joins on the string values.
# Payload schemas live in events/payloads.py (the typed face of this contract).
THINKING_DELTA = "llm.thinking_delta"     # {node_id, text, agent}
TOOL_START = "tool.start"                 # {name, args_preview, agent, node_id}
TOOL_END = "tool.end"                     # {name, ok, duration, output_preview, agent, node_id}
CACHE_STAT = "llm.cache_stat"             # {cache_read, cache_write, miss, total}
AWAIT_USER = "user.await"                 # {kind, prompt, node_id, options}
CHECKPOINT_SAVED = "session.checkpoint"   # {path, cycle, reason}

# ── Progress heartbeat (#8 long-stability) ──────────────────────────────────
# Pushed periodically while an agent blocks on a long phase (LLM call or a
# long-running tool such as RunTraining/Bash), so observers (#6/#7) can tell
# "working" from "hung" without the model polling. Liveness only — never a
# control signal.
HEARTBEAT = "progress.heartbeat"          # {agent, node_id, operation, elapsed_seconds, detail}
