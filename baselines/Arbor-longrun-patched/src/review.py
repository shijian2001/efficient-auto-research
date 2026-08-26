"""review-research CLI — Browse and review past research runs.

Usage:
    review-research --cwd ./project                        # list all runs
    review-research --cwd ./project latest                 # most recent run summary
    review-research --cwd ./project <run_name>             # specific run summary
    review-research --cwd ./project <run_name> tree        # show idea tree
    review-research --cwd ./project <run_name> log         # show agent log (last 200 lines)
    review-research --cwd ./project <run_name> dashboard   # generate & open HTML dashboard
"""

from __future__ import annotations

import json
import sys
import webbrowser
from pathlib import Path


def _resolve_log_dir(cwd: str) -> Path:
    """Find the research sessions directory.

    Searches: <cwd>/../research_sessions/<benchmark>/, then <cwd>/research_logs/
    as fallback for older layouts.
    """
    cwd_p = Path(cwd).resolve()
    benchmark = cwd_p.name

    candidates = [
        cwd_p.parent / "research_sessions" / benchmark,
        cwd_p / "research_logs",
        cwd_p / "research_sessions",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]


def _fmt_score(val) -> str:
    if val is None:
        return "N/A"
    return f"{val:.1f}%"


def _fmt_duration(dur_s: int | float | None) -> str:
    if dur_s is None:
        return "?"
    m, s = divmod(int(dur_s), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def _fmt_size(size: int) -> str:
    if size > 1024 * 1024:
        return f"{size / 1024 / 1024:.1f}MB"
    if size > 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size}B"


def _resolve_run_dir(log_dir: Path, run_name: str) -> Path | None:
    """Resolve 'latest' or a specific run name to a directory."""
    if run_name == "latest":
        runs = sorted([d for d in log_dir.iterdir() if d.is_dir()], reverse=True)
        return runs[0] if runs else None
    d = log_dir / run_name
    return d if d.exists() else None


def list_runs(log_dir: Path) -> None:
    if not log_dir.exists():
        print(f"No research logs found in {log_dir}")
        return

    runs = sorted([d for d in log_dir.iterdir() if d.is_dir()], reverse=True)
    if not runs:
        print("No research logs found.")
        return

    print(f"\n{'Run Name':<30} {'Duration':>10} {'Status':>8} {'Nodes':>6}  {'Baseline':>10} {'Trunk':>10}")
    print("-" * 80)

    for run_dir in runs:
        info_path = run_dir / "run_info.json"
        tree_path = run_dir / "idea_tree.json"

        duration, status, nodes, baseline, trunk = "?", "?", "?", "", ""

        if info_path.exists():
            with open(info_path) as f:
                info = json.load(f)
            duration = _fmt_duration(info.get("duration_seconds"))
            ec = info.get("exit_code", "?")
            status = "OK" if ec == 0 else f"ERR({ec})"

        if tree_path.exists():
            with open(tree_path) as f:
                tree = json.load(f)
            nodes = str(len(tree.get("nodes", {})))
            meta = tree.get("meta", {})
            baseline = _fmt_score(meta.get("baseline_score")) if meta.get("baseline_score") is not None else ""
            trunk = _fmt_score(meta.get("trunk_score")) if meta.get("trunk_score") is not None else ""

        print(f"{run_dir.name:<30} {duration:>10} {status:>8} {nodes:>6}  {baseline:>10} {trunk:>10}")

    print(f"\nTotal: {len(runs)} runs in {log_dir}")


def show_summary(run_dir: Path) -> None:
    print(f"\n{'=' * 60}")
    print(f"  Research Run: {run_dir.name}")
    print(f"{'=' * 60}")

    info_path = run_dir / "run_info.json"
    if info_path.exists():
        with open(info_path) as f:
            info = json.load(f)
        print(f"\n  Started:    {info.get('start_time', '?')}")
        print(f"  Ended:      {info.get('end_time', '?')}")
        print(f"  Duration:   {_fmt_duration(info.get('duration_seconds'))}")
        print(f"  Exit code:  {info.get('exit_code', '?')}")
        print(f"  Config:     {info.get('config_file', '?')}")
        print(f"  Git:        {info.get('git_branch', '?')} @ {info.get('git_commit', '?')}")
        extra = info.get("extra_args")
        if extra:
            print(f"  Extra args: {extra}")

    tree_path = run_dir / "idea_tree.json"
    if tree_path.exists():
        with open(tree_path) as f:
            tree = json.load(f)

        meta = tree.get("meta", {})
        nodes = tree.get("nodes", {})

        print(f"\n  Baseline:   {_fmt_score(meta.get('baseline_score'))}")
        print(f"  Trunk:      {_fmt_score(meta.get('trunk_score'))}")

        bl, tr = meta.get("baseline_score"), meta.get("trunk_score")
        if bl is not None and tr is not None:
            print(f"  Improvement: {tr - bl:+.1f}%")

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
            print("\n  Nodes by status:")
            for st, cnt in sorted(status_counts.items()):
                print(f"    {st}: {cnt}")

        if scored_nodes:
            scored_nodes.sort(key=lambda n: n.get("score", 0), reverse=True)
            print("\n  Top results:")
            for n in scored_nodes[:5]:
                print(f"    {n['id']}: {n.get('hypothesis', '?')[:60]}")
                print(f"         score={n['score']:.1f}%, status={n['status']}")
                if n.get("insight"):
                    print(f"         insight: {n['insight'][:80]}")

    report_path = run_dir / "final_report.txt"
    if report_path.exists():
        report = report_path.read_text().strip()
        if report:
            print("\n  Final Report:")
            for line in report.split("\n")[-20:]:
                print(f"    {line}")

    # Hint about dashboard
    dashboard_path = run_dir / "dashboard.html"
    if dashboard_path.exists():
        print(f"\n  Dashboard available: {dashboard_path}")
        print(f"  Open with: review-research --cwd <cwd> {run_dir.name} dashboard")

    print("\n  Files:")
    for f in sorted(run_dir.iterdir()):
        if f.is_file():
            print(f"    {f.name:<30} {_fmt_size(f.stat().st_size):>10}")
    print()


def show_tree(run_dir: Path) -> None:
    md_path = run_dir / "idea_tree.md"
    if md_path.exists():
        print(md_path.read_text())
    else:
        print(f"No idea_tree.md found in {run_dir}")


def show_summary_report(run_dir: Path) -> None:
    summary_path = run_dir / "summary_report.md"
    if summary_path.exists():
        print(summary_path.read_text())
    else:
        print("(No summary_report.md found — showing basic summary)\n")
        show_summary(run_dir)


def show_log(run_dir: Path, lines: int = 200) -> None:
    log_path = run_dir / "full_output.log"
    if not log_path.exists():
        log_path = run_dir / "coordinator.log"

    if log_path.exists():
        all_lines = log_path.read_text().splitlines()
        show = all_lines[-lines:]
        if len(all_lines) > lines:
            print(f"--- Showing last {lines} of {len(all_lines)} lines ---\n")
        print("\n".join(show))
    else:
        print(f"No log files found in {run_dir}")


def generate_or_open_dashboard(run_dir: Path, open_browser: bool = True) -> None:
    """Generate (or regenerate) the HTML dashboard and optionally open it."""
    from .dashboard import generate_dashboard

    tree_path = run_dir / "idea_tree.json"
    if not tree_path.exists():
        print(f"No idea_tree.json found in {run_dir}", file=sys.stderr)
        return

    run_info_path = run_dir / "run_info.json"
    dashboard_path = generate_dashboard(
        tree_json_path=tree_path,
        output_path=run_dir / "dashboard.html",
        run_info_path=run_info_path if run_info_path.exists() else None,
    )
    print(f"Dashboard generated: {dashboard_path}")

    if open_browser:
        url = dashboard_path.resolve().as_uri()
        print(f"Opening in browser: {url}")
        webbrowser.open(url)


def generate_dashboard_from_tree(tree_json_path: str, open_browser: bool = True) -> None:
    """Generate dashboard directly from an idea_tree.json file path."""
    from .dashboard import generate_dashboard

    tree_path = Path(tree_json_path).resolve()
    if not tree_path.exists():
        print(f"File not found: {tree_path}", file=sys.stderr)
        return

    output_path = tree_path.parent / "dashboard.html"
    run_info_path = tree_path.parent / "run_info.json"

    dashboard_path = generate_dashboard(
        tree_json_path=tree_path,
        output_path=output_path,
        run_info_path=run_info_path if run_info_path.exists() else None,
    )
    print(f"Dashboard generated: {dashboard_path}")

    if open_browser:
        url = dashboard_path.resolve().as_uri()
        print(f"Opening in browser: {url}")
        webbrowser.open(url)


def cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Review past research runs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  review-research --cwd ./project
  review-research --cwd ./project latest
  review-research --cwd ./project latest dashboard
  review-research --cwd ./project latest tree
  review-research --cwd ./project run_20260416_120000 log 500
  review-research --tree ./path/to/idea_tree.json         # direct file mode
""",
    )
    parser.add_argument("--cwd", default=None, help="Target codebase directory")
    parser.add_argument("--tree", default=None, help="Direct path to idea_tree.json (skips run lookup)")
    parser.add_argument("--no-open", action="store_true", help="Don't auto-open dashboard in browser")
    parser.add_argument("run_name", nargs="?", default=None, help="Run name or 'latest'")
    parser.add_argument("subcmd", nargs="?", default=None, choices=["tree", "log", "summary", "dashboard"], help="Sub-command")
    parser.add_argument("lines", nargs="?", type=int, default=200, help="Lines to show (for log)")

    args = parser.parse_args()

    if args.tree:
        generate_dashboard_from_tree(args.tree, open_browser=not args.no_open)
        return

    if not args.cwd:
        parser.error("--cwd is required (or use --tree for direct file mode)")

    log_dir = _resolve_log_dir(args.cwd)

    if args.run_name is None:
        list_runs(log_dir)
        return

    run_dir = _resolve_run_dir(log_dir, args.run_name)
    if run_dir is None:
        print(f"Run '{args.run_name}' not found in {log_dir}", file=sys.stderr)
        sys.exit(1)

    if args.subcmd == "tree":
        show_tree(run_dir)
    elif args.subcmd == "log":
        show_log(run_dir, args.lines)
    elif args.subcmd == "summary":
        show_summary_report(run_dir)
    elif args.subcmd == "dashboard":
        generate_or_open_dashboard(run_dir, open_browser=not args.no_open)
    else:
        show_summary(run_dir)


if __name__ == "__main__":
    cli()
