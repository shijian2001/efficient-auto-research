"""Coarse trajectory export for self-evolution (line 1).

Joins ``events.jsonl`` + the idea tree into one ``trajectory.jsonl`` — one line
per decision point (a proposed idea, its execution, and the merge/prune
decision), reward back-filled from eval. Pure transform: no LLM calls, no
network. Token-faithful per-call traces (Polar-style) are a separate, opt-in
layer; see ``docs/dev/trajectory-export.md``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .export import resolve_session_dir  # reuse the existing resolver

TRAJECTORY_FILENAME = "trajectory.jsonl"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _load_tree(session_dir: Path) -> dict[str, dict[str, Any]]:
    for rel in (".coordinator/idea_tree.json", "idea_tree.json"):
        p = session_dir / rel
        if p.exists():
            try:
                nodes = json.loads(p.read_text(encoding="utf-8")).get("nodes", {})
            except json.JSONDecodeError:
                return {}
            return nodes if isinstance(nodes, dict) else {n.get("id"): n for n in nodes}
    return {}


def build_trajectory(session_dir: Path) -> list[dict[str, Any]]:
    """Reconstruct ordered decision-point records for one session."""
    session_dir = Path(session_dir)
    events = _load_jsonl(session_dir / "events.jsonl")
    tree = _load_tree(session_dir)
    run = session_dir.name

    cycle_of: dict[str, int] = {}
    cur_cycle = 0
    proposed: dict[str, dict[str, Any]] = {}      # node_id -> action payload
    rewards: dict[str, dict[str, Any]] = {}        # node_id -> reward
    order: list[str] = []

    for ev in events:
        t, d = ev.get("type"), ev.get("data", {})
        if t == "cycle.start":
            cur_cycle = d.get("cycle_num", cur_cycle)
            nid = d.get("node_id")
            if nid:
                cycle_of[nid] = cur_cycle
        elif t == "idea.proposed":
            nid = d.get("node_id")
            if nid and nid not in proposed:
                proposed[nid] = {"hypothesis": d.get("hypothesis", ""), "parent_id": d.get("parent_id")}
                cycle_of.setdefault(nid, cur_cycle)
                order.append(nid)
        elif t in ("eval.end", "executor.end", "idea.completed"):
            nid = d.get("node_id")
            if nid:
                r = rewards.setdefault(nid, {})
                if d.get("score") is not None:
                    r["dev_score"] = d["score"]
                r.setdefault("merged", False)
                if d.get("tokens") is not None:
                    r["tokens"] = d["tokens"]
        elif t == "idea.merged":
            nid = d.get("node_id")
            if nid:
                rewards.setdefault(nid, {})["merged"] = True

    records: list[dict[str, Any]] = []
    for step, nid in enumerate(order):
        node = tree.get(nid, {})
        records.append({
            "run": run,
            "step": step,
            "node_id": nid,
            "parent_id": proposed[nid].get("parent_id"),
            "cycle": cycle_of.get(nid, 0),
            "action": {"kind": "ideate", "hypothesis": proposed[nid]["hypothesis"]},
            "reward": rewards.get(nid) or None,
            "outcome": {"status": node.get("status"), "score": node.get("score")},
            "insight": node.get("insight", ""),
        })
    return records


def write_trajectory(session_dir: Path) -> Path:
    """Write ``trajectory.jsonl`` into the session dir; return its path."""
    session_dir = Path(session_dir)
    records = build_trajectory(session_dir)
    out = session_dir / TRAJECTORY_FILENAME
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
    return out


def export_trajectory(session: str, cwd: Path | None = None) -> Path:
    """Resolve a session name/path and write its trajectory (CLI/offline use)."""
    return write_trajectory(resolve_session_dir(Path(session), cwd))


def append_token_record(
    path: str | Path,
    *,
    messages: list[dict[str, Any]],
    response: Any,
    turn: int,
    model: str,
) -> None:
    """Append one token-level call record (Polar-style) to ``tokens.jsonl``.

    Captures the request context and the model output for SFT. logprobs/token_ids
    are filled only when the provider returns them (OpenAI); Anthropic gives
    none, so we record null rather than fake it. Best-effort — never raise.
    """
    try:
        u = getattr(response, "usage", None)
        rec = {
            "turn": turn,
            "model": model,
            "messages": messages,
            "output": getattr(response, "raw_content", None),
            "input_tokens": int(getattr(u, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(u, "output_tokens", 0) or 0),
            "logprobs": getattr(response, "logprobs", None),  # None unless provider gives it
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # pylint: disable=broad-exception-caught
        pass
