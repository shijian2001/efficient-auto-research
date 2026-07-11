#!/usr/bin/env python3
"""
扫描 EAR 的 docker_runs/，把各次运行的 report.json 汇成一张对比表。

每次评测跑产出 docker_runs/<RUN_TAG>_<comp>/workspace/report.json，
自 git-stamp 改动起其中含 git_commit/git_branch，可反查代码版本。

用法:
    python scripts/compare_runs.py                 # 扫默认 docker_runs/
    python scripts/compare_runs.py <dir> [<dir>..] # 指定一个或多个 docker_runs 目录
    python scripts/compare_runs.py --filter mlsp   # 只看 tag 含 mlsp 的
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def find_reports(roots: list[Path]):
    for root in roots:
        if not root.exists():
            continue
        for rep in sorted(root.glob("*/workspace/report.json")):
            yield rep


def best_first_step(step_log: list, best: float | None):
    """best_metric 首次达到是在第几步 (看停滞用)。"""
    if best is None:
        return None
    for s in step_log:
        bs = s.get("best_so_far")
        if bs is not None and abs(bs - best) < 1e-9:
            return s.get("step")
    return None


def load(rep: Path) -> dict | None:
    try:
        r = json.loads(rep.read_text())
    except Exception as e:
        print(f"  (跳过 {rep}: {e})", file=sys.stderr)
        return None
    # tag = docker_runs/<tag>/workspace/report.json → 取 <tag>
    tag = rep.parent.parent.name
    sl = r.get("step_log", [])
    best = r.get("best_metric")
    return {
        "tag": tag,
        "commit": (r.get("git_commit") or "-")[:8],
        "branch": r.get("git_branch") or "-",
        "dirty": {True: "*", False: "", None: "?"}[r.get("git_dirty")],
        "steps": r.get("total_steps"),
        "best": best,
        "best_step": best_first_step(sl, best),
        "hours": (r.get("total_time_seconds") or 0) / 3600,
        "ktok": (r.get("total_tokens") or 0) / 1000,
    }


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    flt = None
    for a in sys.argv[1:]:
        if a.startswith("--filter"):
            flt = a.split("=", 1)[1] if "=" in a else None
    # 支持 --filter <val> 空格形式
    if "--filter" in sys.argv[1:]:
        i = sys.argv.index("--filter")
        if i + 1 < len(sys.argv):
            flt = sys.argv[i + 1]
            argv = [a for a in argv if a != flt]

    roots = [Path(a) for a in argv] or [REPO_ROOT / "docker_runs"]

    rows = []
    for rep in find_reports(roots):
        row = load(rep)
        if row is None:
            continue
        if flt and flt not in row["tag"]:
            continue
        rows.append(row)

    if not rows:
        print("没有找到 report.json (检查 docker_runs/ 路径或 --filter)")
        return

    rows.sort(key=lambda r: r["tag"])

    hdr = f"{'tag':<52} {'commit':<9}{'br':<18}{'steps':>6}{'best':>10}{'@step':>6}{'hrs':>6}{'ktok':>9}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        best = f"{r['best']:.4f}" if r["best"] is not None else "-"
        print(
            f"{r['tag'][:52]:<52} "
            f"{r['commit']+r['dirty']:<9}"
            f"{r['branch'][:16]:<18}"
            f"{str(r['steps']):>6}"
            f"{best:>10}"
            f"{str(r['best_step']):>6}"
            f"{r['hours']:>6.1f}"
            f"{r['ktok']:>9.0f}"
        )
    print("\n* = git_dirty (有未提交改动)   ? = 无 git 信息 (老结果)")


if __name__ == "__main__":
    main()
