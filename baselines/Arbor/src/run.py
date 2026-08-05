"""run-research CLI — Launch coordinator with full logging.

Wraps `coordinator` with structured log capture so every run is
saved to <cwd>/../research_sessions/<benchmark>/<run_name>/ for review.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _generate_summary_report(
    log_dir: Path,
    run_info: dict,
    dur_str: str,
    trunk_branch: str,
) -> None:
    """Generate a human-readable summary_report.md in the log directory."""
    lines: list[str] = []

    lines.append(f"# Research Run: {run_info['run_name']}\n")
    lines.append(f"- **Started**: {run_info.get('start_time', '?')}")
    lines.append(f"- **Ended**: {run_info.get('end_time', '?')}")
    lines.append(f"- **Duration**: {dur_str}")
    lines.append(f"- **Exit code**: {run_info.get('exit_code', '?')}")
    lines.append(f"- **Config**: {run_info.get('config_file', '?')}")
    lines.append(f"- **CWD**: {run_info.get('cwd', '?')}")
    lines.append(f"- **Git**: {run_info.get('git_branch', '?')} @ {run_info.get('git_commit', '?')}")
    lines.append(f"- **Trunk branch**: {trunk_branch}")
    lines.append("")

    # Run stats (tokens, LLM calls, agents spawned)
    stats_path = log_dir / "run_stats.json"
    stats: dict = {}
    if stats_path.exists():
        try:
            stats = json.loads(stats_path.read_text())
        except (json.JSONDecodeError, OSError):
            stats = {}
    if stats:
        all_a = stats.get("all_agents", {}) or {}
        meta_a = stats.get("coordinator", {}) or {}
        iters = stats.get("iterations", {}) or {}

        def _fmt_int(v: Any) -> str:
            return f"{v:,}" if isinstance(v, int) else str(v if v is not None else "?")

        lines.append("## Run Stats\n")
        if stats.get("token_scope"):
            lines.append(f"_Token scope_: `{stats['token_scope']}`")
            lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Wall-clock time | {stats.get('duration_human', dur_str)} |")
        lines.append(f"| Model | {stats.get('model', '?')} |")
        lines.append(f"| Total LLM calls | {_fmt_int(all_a.get('total_llm_calls'))} |")
        lines.append(f"| Total agents spawned | {_fmt_int(all_a.get('total_agents_spawned'))} |")
        lines.append(f"| Total input tokens | {_fmt_int(all_a.get('total_input_tokens'))} |")
        if all_a.get("total_uncached_input_tokens") is not None:
            lines.append(f"| Total uncached input tokens | {_fmt_int(all_a.get('total_uncached_input_tokens'))} |")
        if all_a.get("total_cache_read_tokens"):
            lines.append(f"| Total cache-read input tokens | {_fmt_int(all_a.get('total_cache_read_tokens'))} |")
        if all_a.get("total_cache_creation_tokens"):
            lines.append(f"| Total cache-creation input tokens | {_fmt_int(all_a.get('total_cache_creation_tokens'))} |")
        lines.append(f"| Total output tokens | {_fmt_int(all_a.get('total_output_tokens'))} |")
        lines.append(f"| Total tokens | {_fmt_int(all_a.get('total_tokens'))} |")
        lines.append(f"| Coordinator turns | {_fmt_int(meta_a.get('turns'))} |")
        lines.append(
            f"| Coordinator tokens (in/out) | "
            f"{_fmt_int(meta_a.get('input_tokens'))} / {_fmt_int(meta_a.get('output_tokens'))} |"
        )
        lines.append(
            f"| Iteration nodes (total / scored) | "
            f"{_fmt_int(iters.get('total_nodes'))} / {_fmt_int(iters.get('scored_nodes'))} |"
        )
        if stats.get("emergency_timeout"):
            lines.append("| Emergency timeout | yes |")
        lines.append("")

    # Scores from idea tree
    tree_path = log_dir / "idea_tree.json"
    if tree_path.exists():
        try:
            tree = json.loads(tree_path.read_text())
            meta = tree.get("meta", {})
            nodes = tree.get("nodes", {})

            # Primary metric: TEST scores (what the user cares about)
            test_bl = meta.get("test_baseline_score")
            test_tr = meta.get("test_trunk_score")
            bl = meta.get("baseline_score")
            tr = meta.get("trunk_score")

            lines.append("## Results\n")

            if test_bl is not None or test_tr is not None:
                lines.append("### Test Set (Primary Metric)\n")
                lines.append("| Metric | Value |")
                lines.append("|--------|-------|")
                if test_bl is not None:
                    lines.append(f"| Baseline | {test_bl:.1f}% |")
                if test_tr is not None:
                    lines.append(f"| **Final** | **{test_tr:.1f}%** |")
                if test_bl is not None and test_tr is not None:
                    lines.append(f"| **Improvement** | **{test_tr - test_bl:+.1f}%** |")
                lines.append("")

            if bl is not None or tr is not None:
                lines.append("### Dev Set (Iteration)\n")
                lines.append("| Metric | Value |")
                lines.append("|--------|-------|")
                if bl is not None:
                    lines.append(f"| Baseline | {bl:.1f}% |")
                if tr is not None:
                    lines.append(f"| Final | {tr:.1f}% |")
                if bl is not None and tr is not None:
                    lines.append(f"| Improvement | {tr - bl:+.1f}% |")
                lines.append("")

            # Node statistics
            status_counts: dict[str, int] = {}
            scored_nodes = []
            for node in nodes.values():
                if node.get("depth", 0) == 0:
                    continue
                st = node.get("status", "?")
                status_counts[st] = status_counts.get(st, 0) + 1
                if node.get("score") is not None:
                    scored_nodes.append(node)

            if status_counts:
                lines.append("## Node Statistics\n")
                for st, cnt in sorted(status_counts.items()):
                    lines.append(f"- **{st}**: {cnt}")
                lines.append("")

            # Experiment results table (sorted by score, with links to reports)
            if scored_nodes:
                scored_nodes.sort(key=lambda n: n.get("score", 0), reverse=True)
                lines.append("## All Experiments\n")
                lines.append("| Rank | Node | Score | Status | Hypothesis | Insight |")
                lines.append("|------|------|-------|--------|-----------|---------|")
                for i, n in enumerate(scored_nodes, 1):
                    nid = n["id"]
                    hyp = n.get("hypothesis", "?")[:60]
                    insight = (n.get("insight") or "")[:60]
                    exp_link = f"[{nid}](experiments/{nid}/report.md)"
                    split = n.get("score_split", "dev")
                    lines.append(
                        f"| {i} | {exp_link} | {n['score']:.1f}% ({split}) | {n['status']} | {hyp} | {insight} |"
                    )
                lines.append("")

            # Global insight
            root = nodes.get(tree.get("root_id", "ROOT"), {})
            if root.get("insight"):
                lines.append("## Key Insights\n")
                lines.append(root["insight"])
                lines.append("")

        except (json.JSONDecodeError, KeyError):
            lines.append("*(Could not parse idea_tree.json)*\n")

    # Idea tree visualization
    tree_md = log_dir / "idea_tree.md"
    if tree_md.exists():
        lines.append("## Idea Tree\n")
        lines.append(tree_md.read_text())
        lines.append("")

    # Next steps
    lines.append("## Next Steps\n")
    lines.append("To merge improvements into main:")
    lines.append("```bash")
    lines.append(f"cd {run_info.get('cwd', '.')}")
    lines.append(f"git log {trunk_branch} --oneline  # review changes")
    lines.append(f"git merge {trunk_branch}           # merge to main")
    lines.append("```")

    summary_path = log_dir / "summary_report.md"
    summary_path.write_text("\n".join(lines))
    print(f"  Saved: {summary_path}")


def _generate_research_readme(log_dir: Path, run_info: dict, dur_str: str) -> None:
    """Write a high-level README.md summarising effective exploration paths.

    Highlights what worked, the agent's key thoughts/insights, and the
    chain of hypotheses behind the best-scoring experiments. Intended as
    the first thing a human reads when revisiting a research session.
    """
    lines: list[str] = []
    benchmark = Path(run_info.get("cwd", "")).name or "?"
    lines.append(f"# Research Session — {run_info['run_name']}\n")
    lines.append(f"**Benchmark**: `{benchmark}`  ")
    lines.append(f"**Duration**: {dur_str}  ")
    lines.append(f"**Started**: {run_info.get('start_time', '?')}  ")
    lines.append(f"**Trunk branch**: `{run_info.get('trunk_branch', '?')}`  ")
    lines.append("")

    # Stats one-liner
    stats_path = log_dir / "run_stats.json"
    stats: dict = {}
    if stats_path.exists():
        try:
            stats = json.loads(stats_path.read_text())
        except (json.JSONDecodeError, OSError):
            stats = {}
    if stats:
        all_a = stats.get("all_agents", {}) or {}
        iters = stats.get("iterations", {}) or {}
        lines.append(
            f"**Research-agent LLM cost**: {all_a.get('total_llm_calls', '?')} calls · "
            f"{all_a.get('total_input_tokens', 0):,} in / "
            f"{all_a.get('total_output_tokens', 0):,} out tokens · "
            f"{all_a.get('total_agents_spawned', '?')} agent instances  "
        )
        lines.append(
            f"**Iterations**: {iters.get('total_nodes', '?')} idea nodes "
            f"({iters.get('scored_nodes', '?')} scored)"
        )
        lines.append("")

    # Headline scoreboard
    tree_path = log_dir / "idea_tree.json"
    tree: dict = {}
    nodes: dict = {}
    if tree_path.exists():
        try:
            tree = json.loads(tree_path.read_text())
            nodes = tree.get("nodes", {})
        except (json.JSONDecodeError, OSError):
            pass

    meta = tree.get("meta", {}) if tree else {}
    test_bl = meta.get("test_baseline_score")
    test_tr = meta.get("test_trunk_score")
    bl = meta.get("baseline_score")
    tr = meta.get("trunk_score")
    lines.append("## Headline Result\n")
    if test_bl is not None or test_tr is not None:
        lines.append(f"- Test set: baseline `{test_bl}` → final `{test_tr}`")
    if bl is not None or tr is not None:
        delta = ""
        if bl is not None and tr is not None:
            delta = f"  (Δ {tr - bl:+.1f}%)"
        lines.append(f"- Dev set:  baseline `{bl}` → final `{tr}`{delta}")
    lines.append("")

    # Effective exploration: top-scoring done/merged nodes with parent chain
    direction = (meta.get("metric_direction") or "maximize").lower()
    scored = [
        n for n in nodes.values()
        if n.get("depth", 0) > 0
        and n.get("score") is not None
        and n.get("status") in ("done", "merged")
    ]
    scored.sort(key=lambda n: n.get("score", 0), reverse=(direction != "minimize"))

    lines.append("## What Worked (Effective Exploration)\n")
    if not scored:
        lines.append("_No scored experiments completed._\n")
    else:
        baseline_for_delta = bl if bl is not None else 0.0
        for i, n in enumerate(scored[:5], 1):
            nid = n.get("id", "?")
            score = n.get("score")
            hyp = (n.get("hypothesis") or "").strip()
            insight = (n.get("insight") or "").strip()
            result = (n.get("result") or "").strip()
            status = n.get("status", "?")
            delta_txt = ""
            if isinstance(score, (int, float)):
                delta_txt = f" (Δ {score - baseline_for_delta:+.1f}% vs baseline)"

            # Parent chain (excluding ROOT)
            chain: list[str] = []
            cur = n
            while cur and cur.get("parent_id") and cur.get("parent_id") != tree.get("root_id"):
                parent = nodes.get(cur["parent_id"])
                if not parent or parent.get("depth", 0) == 0:
                    break
                chain.append(parent.get("hypothesis", "?"))
                cur = parent
            chain.reverse()

            split = n.get("score_split", "dev")
            lines.append(f"### {i}. Node `{nid}` — score `{score}` ({split}, {status}){delta_txt}\n")
            lines.append(f"**Hypothesis**: {hyp}\n")
            if chain:
                lines.append("**Lineage**:")
                for j, h in enumerate(chain):
                    lines.append(f"{'  ' * j}- {h}")
                lines.append("")
            if result:
                lines.append(f"**Result**: {result}\n")
            if insight:
                lines.append(f"**Insight**: {insight}\n")
            report_link = f"experiments/{nid}/report.md"
            if (log_dir / report_link).exists():
                lines.append(f"[Full experiment report]({report_link})\n")
            lines.append("")

    # Dead ends — pruned/failing branches (brief)
    pruned = [
        n for n in nodes.values()
        if n.get("depth", 0) > 0 and n.get("status") == "pruned"
    ]
    if pruned:
        lines.append("## Pruned / Dead Ends\n")
        for n in pruned[:8]:
            hyp = (n.get("hypothesis") or "").strip()[:120]
            insight = (n.get("insight") or "").strip()[:160]
            line = f"- `{n.get('id', '?')}`: {hyp}"
            if insight:
                line += f" — _{insight}_"
            lines.append(line)
        lines.append("")

    # Agent's key thoughts / global insight
    root = nodes.get(tree.get("root_id", "ROOT"), {}) if tree else {}
    if root.get("insight"):
        lines.append("## Agent's Key Thoughts (Global Insight)\n")
        lines.append(root["insight"].strip())
        lines.append("")

    # Pointers
    lines.append("## Files in this Session\n")
    artifacts = [
        ("summary_report.md", "Detailed run report (stats, all experiments, idea tree)"),
        ("run_stats.json", "Machine-readable usage stats (tokens, calls, iterations)"),
        ("run_info.json", "Run metadata (timing, git, config)"),
        ("idea_tree.json", "Canonical idea tree state"),
        ("idea_tree.md", "Human-readable idea tree"),
        ("dashboard.html", "Interactive HTML dashboard"),
        ("config_snapshot.yaml", "Config used for this run"),
        ("full_output.log", "Full stdout/stderr capture"),
        ("experiments/", "Per-experiment reports, metrics, and diffs"),
        ("results_snapshot/", "Snapshot of metrics.json from each results/ subdir"),
    ]
    for name, desc in artifacts:
        if (log_dir / name).exists():
            lines.append(f"- [`{name}`]({name}) — {desc}")
    lines.append("")

    out = log_dir / "README.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Saved: {out}")


def _snapshot_user_config(src: str, dst: "Path") -> None:
    """Copy a user YAML into the run log with secrets masked.

    The coordinator writes the authoritative fully-resolved snapshot into
    ``.coordinator/``; this launcher-side copy mirrors the user's file but must
    never persist plaintext credentials.
    """
    try:
        import yaml

        from .core.config_schema import redact_secrets

        data = yaml.safe_load(Path(src).read_text(encoding="utf-8"))
        Path(dst).write_text(
            yaml.safe_dump(redact_secrets(data), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    except (OSError, ValueError, ImportError) as exc:
        print(f"  Warning: failed to snapshot config: {exc}", file=sys.stderr)


def cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Launch coordinator with full logging",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  run-research --cwd ./browsecomp --config research_config.yaml
  run-research --cwd ./browsecomp --config research_config.yaml --run-name prompt_v2
  run-research --cwd ./browsecomp --config research_config.yaml --resume
  run-research --cwd ./browsecomp --config research_config.yaml -- --max-cycles 5
""",
    )
    parser.add_argument("--cwd", required=True, help="Target codebase directory")
    parser.add_argument("--config", default=None, help="Path to YAML config file")
    parser.add_argument("--run-name", default=None, help="Custom run name (default: run_<timestamp>)")
    parser.add_argument("--workspace-dir", default=None, help="Override workspace directory for logs (default: <cwd>/../research_sessions/<benchmark>/)")
    parser.add_argument("--resume", action="store_true", help="Resume from existing .coordinator/ tree")
    parser.add_argument(
        "extra_args",
        nargs="*",
        help="Extra arguments passed through to coordinator (put after --)",
    )

    args = parser.parse_args()
    cwd = os.path.abspath(args.cwd)

    if not os.path.isdir(cwd):
        print(f"Error: {cwd} is not a directory", file=sys.stderr)
        sys.exit(1)

    # ── Run name & log directory ────────────────────────────────
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = args.run_name or f"run_{timestamp}"
    benchmark_name = Path(cwd).name

    if args.workspace_dir:
        log_dir = Path(args.workspace_dir) / run_name
    else:
        log_dir = Path(cwd).parent / "research_sessions" / benchmark_name / run_name
    log_dir.mkdir(parents=True, exist_ok=True)
    experiments_dir = log_dir / "experiments"
    experiments_dir.mkdir(exist_ok=True)

    # ── Branch prefix and working trunk ────────────────────────
    branch_prefix = f"research/{run_name}"
    trunk_branch = f"{branch_prefix}/trunk"

    # Create the working trunk branch from current HEAD (keeps main clean)
    try:
        subprocess.check_call(
            ["git", "branch", trunk_branch],
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        # Branch may already exist (e.g., resuming)
        pass

    # ── Snapshot config (redact secrets; the coordinator also writes a
    #    fully-resolved snapshot into .coordinator/) ────────────────────
    config_path = None
    if args.config:
        config_path = os.path.abspath(args.config)
        if os.path.isfile(config_path):
            _snapshot_user_config(config_path, log_dir / "config_snapshot.yaml")

    # ── Gather git info ─────────────────────────────────────────
    def _git(cmd: str) -> str:
        try:
            return subprocess.check_output(
                cmd, shell=True, cwd=cwd, stderr=subprocess.DEVNULL, text=True
            ).strip()
        except (subprocess.SubprocessError, OSError):
            return "N/A"

    # Pre-flight: ensure repo is clean
    dirty = _git("git status --porcelain")
    if dirty:
        print("ERROR: Repository has uncommitted changes:", file=sys.stderr)
        for line in dirty.splitlines()[:10]:
            print(f"  {line}", file=sys.stderr)
        print(
            "Commit or stash changes before starting a research run "
            "to ensure main stays clean.",
            file=sys.stderr,
        )
        sys.exit(1)

    git_main_branch = _git("git branch --show-current")

    run_info = {
        "run_name": run_name,
        "start_time": datetime.now(timezone.utc).isoformat(),
        "config_file": args.config or "N/A",
        "extra_args": args.extra_args,
        "cwd": cwd,
        "trunk_branch": trunk_branch,
        "branch_prefix": branch_prefix,
        "git_branch": git_main_branch,
        "git_commit": _git("git rev-parse --short HEAD"),
    }
    (log_dir / "run_info.json").write_text(json.dumps(run_info, indent=2))

    # ── Build coordinator command ────────────────────────────────
    cmd = [sys.executable, "-m", "arbor.coordinator.main", "--cwd", cwd, "-v"]
    cmd += ["--branch-prefix", branch_prefix]
    cmd += ["--trunk-branch", trunk_branch]
    cmd += ["--workspace-dir", str(log_dir)]
    if config_path:
        cmd += ["--config", config_path]
    if args.resume:
        cmd += ["--resume"]
    cmd += args.extra_args

    # ── Print banner ────────────────────────────────────────────
    print("=" * 60)
    print(f"  Research Run: {run_name}")
    print(f"  Benchmark:    {benchmark_name}")
    print(f"  Config:       {args.config or '(none)'}")
    print(f"  CWD:          {cwd}")
    print(f"  Trunk branch: {trunk_branch}")
    print(f"  Branch prefix:{branch_prefix}")
    print(f"  Workspace:    {log_dir}")
    print(f"  Started:      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print(flush=True)

    # ── Run with logging ────────────────────────────────────────
    full_log = log_dir / "full_output.log"
    t0 = time.monotonic()
    exit_code = 0

    try:
        with open(full_log, "w", encoding="utf-8") as log_f:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=cwd,
                bufsize=1,
            )
            for line in proc.stdout:
                ts = datetime.now().strftime("%H:%M:%S")
                stamped = f"[{ts}] {line}"
                sys.stdout.write(stamped)
                sys.stdout.flush()
                log_f.write(stamped)
                log_f.flush()
            proc.wait()
            exit_code = proc.returncode
    except KeyboardInterrupt:
        print("\n[Interrupted by user]")
        exit_code = 130
    except (OSError, subprocess.SubprocessError) as e:
        print(f"\n[Error: {e}]", file=sys.stderr)
        exit_code = 1

    duration = int(time.monotonic() - t0)
    m, s = divmod(duration, 60)
    h, m = divmod(m, 60)
    dur_str = f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"

    print()
    print("=" * 60)
    print(f"  Run completed in {dur_str} (exit code: {exit_code})")
    print("=" * 60)

    # ── Snapshot artifacts ──────────────────────────────────────
    cwd_p = Path(cwd)
    meta_dirs = [log_dir / ".coordinator", cwd_p / ".coordinator"]
    artifacts: list[tuple[Path, str]] = []
    for md in meta_dirs:
        if md.exists():
            artifacts.append((md / "idea_tree.json", "idea_tree.json"))
            artifacts.append((md / "idea_tree.md", "idea_tree.md"))
            break
    artifacts.append((cwd_p / ".arbor" / "experiments.jsonl", "experiments.jsonl"))

    for src, dst in artifacts:
        if src.exists():
            shutil.copy2(src, log_dir / dst)
            print(f"  Saved: {log_dir / dst}")

    # ── Snapshot result metrics ─────────────────────────────────
    results_dir = cwd_p / "results"
    if results_dir.is_dir():
        snap_dir = log_dir / "results_snapshot"
        snap_dir.mkdir(exist_ok=True)
        for d in results_dir.iterdir():
            if d.is_dir() and (d / "metrics.json").exists():
                shutil.copy2(d / "metrics.json", snap_dir / f"{d.name}_metrics.json")

    # ── Update run_info ─────────────────────────────────────────
    run_info["end_time"] = datetime.now(timezone.utc).isoformat()
    run_info["duration_seconds"] = duration
    run_info["exit_code"] = exit_code
    (log_dir / "run_info.json").write_text(json.dumps(run_info, indent=2))

    # ── Generate summary_report.md ─────────────────────────────
    _generate_summary_report(log_dir, run_info, dur_str, trunk_branch)

    # ── Generate top-level README.md ───────────────────────────
    _generate_research_readme(log_dir, run_info, dur_str)

    # ── Generate HTML dashboard ────────────────────────────────
    tree_json_for_dashboard = log_dir / "idea_tree.json"
    if tree_json_for_dashboard.exists():
        try:
            from .dashboard import generate_dashboard
            dashboard_path = generate_dashboard(
                tree_json_path=tree_json_for_dashboard,
                output_path=log_dir / "dashboard.html",
                run_info_path=log_dir / "run_info.json",
            )
            print(f"  Dashboard: {dashboard_path}")
        except (ImportError, OSError, ValueError) as e:
            print(f"  Warning: dashboard generation failed: {e}", file=sys.stderr)

    # ── Post-run: verify main branch is clean ──────────────────
    if git_main_branch in ("main", "master"):
        try:
            subprocess.check_call(
                ["git", "checkout", git_main_branch],
                cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

            # Commit baseline cache to main if it was newly created on trunk
            baseline_file = ".research_baseline.json"
            workspace_baseline_cache = log_dir / ".coordinator" / "baseline_cache.json"
            baseline_results_dir = "results/init"
            files_to_commit: list[str] = []

            def _git_path_exists(ref: str, path: str) -> bool:
                try:
                    subprocess.check_call(
                        ["git", "cat-file", "-e", f"{ref}:{path}"],
                        cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    return True
                except subprocess.CalledProcessError:
                    return False

            # Check .research_baseline.json
            main_has_baseline = _git_path_exists("HEAD", baseline_file)
            if not main_has_baseline:
                if workspace_baseline_cache.exists():
                    shutil.copy2(workspace_baseline_cache, os.path.join(cwd, baseline_file))
                    files_to_commit.append(baseline_file)
                else:
                    try:
                        subprocess.check_call(
                            ["git", "checkout", trunk_branch, "--", baseline_file],
                            cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        )
                        if os.path.exists(os.path.join(cwd, baseline_file)):
                            files_to_commit.append(baseline_file)
                    except subprocess.CalledProcessError:
                        pass

            # Check results/init/ (baseline result files)
            main_has_results = _git_path_exists("HEAD", baseline_results_dir)
            if not main_has_results:
                if os.path.isdir(os.path.join(cwd, baseline_results_dir)):
                    files_to_commit.append(baseline_results_dir)
                else:
                    try:
                        subprocess.check_call(
                            ["git", "checkout", trunk_branch, "--", baseline_results_dir],
                            cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        )
                        if os.path.isdir(os.path.join(cwd, baseline_results_dir)):
                            files_to_commit.append(baseline_results_dir)
                    except subprocess.CalledProcessError:
                        pass

            if files_to_commit:
                try:
                    cache_data = json.loads(
                        Path(os.path.join(cwd, baseline_file)).read_text(encoding="utf-8")
                    ) if os.path.exists(os.path.join(cwd, baseline_file)) else {}
                    score = cache_data.get("baseline_score", "?")
                    msg = f"arbor: cache baseline results (score={score})"
                except (json.JSONDecodeError, OSError):
                    msg = "arbor: cache baseline results"
                try:
                    for f in files_to_commit:
                        subprocess.check_call(
                            ["git", "add", f],
                            cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        )
                    subprocess.check_call(
                        ["git", "commit", "-m", msg],
                        cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    print(f"  Committed baseline to {git_main_branch}: {msg} ({', '.join(files_to_commit)})")
                except subprocess.CalledProcessError:
                    pass

            post_dirty = _git("git status --porcelain")
            if post_dirty:
                # Safety-net cleanup: remove untracked dirs/files that the run may have leaked
                _cleanup_dirs = [".arbor", "submissions", "models", "logs", "analysis", "runs", "cache"]
                _cleanup_files = ["submission.csv"]
                cleaned = []
                for d in _cleanup_dirs:
                    dp = cwd_p / d
                    if dp.is_dir() and f"?? {d}/" in post_dirty:
                        shutil.rmtree(dp, ignore_errors=True)
                        cleaned.append(d + "/")
                for f in _cleanup_files:
                    fp = cwd_p / f
                    if fp.is_file() and f"?? {f}" in post_dirty:
                        fp.unlink(missing_ok=True)
                        cleaned.append(f)
                if cleaned:
                    print(f"  Cleaned up leaked artifacts: {', '.join(cleaned)}")

                post_dirty2 = _git("git status --porcelain")
                if post_dirty2:
                    print("\n  WARNING: Main branch has unexpected changes after run:")
                    for line in post_dirty2.splitlines()[:5]:
                        print(f"    {line}")
                    print("  Review and clean up before the next run.")
                else:
                    print("  Main branch is clean.")
            else:
                print("  Main branch is clean.")
        except subprocess.CalledProcessError:
            pass

    print()
    print(f"  All artifacts saved to: {log_dir}/")
    print(f"  README:         {log_dir / 'README.md'}")
    print(f"  Summary report: {log_dir / 'summary_report.md'}")
    if (log_dir / "run_stats.json").exists():
        print(f"  Run stats:      {log_dir / 'run_stats.json'}")
    if (log_dir / "dashboard.html").exists():
        print(f"  Dashboard:      {log_dir / 'dashboard.html'}")
    print(f"  Experiments:    {log_dir / 'experiments/'}")
    print()
    print(f"  Working trunk branch: {trunk_branch}")
    print("  To merge improvements into main:")
    print(f"    cd {cwd}")
    print(f"    git log {trunk_branch} --oneline  # review changes")
    print(f"    git merge {trunk_branch}           # merge to main")
    print()

    sys.exit(exit_code)


if __name__ == "__main__":
    cli()
