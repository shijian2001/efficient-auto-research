"""
MLE-bench evaluation: run agent on competitions and grade with official mlebench grader.

Usage:
    python -m eval.mlebench --agent agent/run.py --competition spaceship-titanic --timeout 600 --max-steps 10
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def _find_mlebench_root() -> Path:
    """Find mle-bench repo root from installed package."""
    import mlebench
    return Path(mlebench.__file__).parent.parent


def prepare_desc_file(competition_id: str, data_dir: str, work_dir: Path) -> Path:
    parts = []
    instructions = _find_mlebench_root() / "environment" / "instructions.txt"
    if instructions.exists():
        parts.append(instructions.read_text())
    comp_desc = Path(data_dir) / competition_id / "prepared" / "public" / "description.md"
    if comp_desc.exists():
        parts.append("\nCOMPETITION INSTRUCTIONS\n------\n")
        parts.append(comp_desc.read_text())
    desc_file = work_dir / "description.md"
    desc_file.write_text("\n".join(parts))
    return desc_file


def grade_submission(competition_id: str, submission_path: Path, data_dir: str) -> dict | None:
    """Grade a submission using mlebench's official grader (must be pip-installed)."""
    if not submission_path.exists():
        return None

    cmd = [
        sys.executable, "-m", "mlebench.cli",
        "grade-sample", str(submission_path), competition_id,
        "--data-dir", data_dir,
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

        # mlebench outputs the JSON report in stderr (via logging)
        # Find the JSON block in combined output
        combined = (proc.stdout or "") + (proc.stderr or "")

        if proc.returncode != 0 and "Invalid submission" in combined:
            return {"error": f"Invalid submission format: {combined.split('Invalid submission:')[-1][:200]}"}

        # Extract JSON from output
        json_start = combined.find("{")
        if json_start >= 0:
            # Find matching closing brace
            depth = 0
            for i in range(json_start, len(combined)):
                if combined[i] == "{":
                    depth += 1
                elif combined[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(combined[json_start:i+1])
                        except json.JSONDecodeError:
                            pass
                        break

        if proc.returncode != 0:
            return {"error": combined[-500:]}
        return {"error": "could not parse grading output"}

    except subprocess.TimeoutExpired:
        return {"error": "grading timed out"}
    except Exception as e:
        return {"error": str(e)}


def run_competition(agent_script: str, competition_id: str, data_dir: str,
                    output_dir: Path, timeout: int, extra_args: list[str]) -> dict:
    comp_data = Path(data_dir) / competition_id / "prepared" / "public"
    work_dir = output_dir / competition_id
    work_dir.mkdir(parents=True, exist_ok=True)

    desc_file = prepare_desc_file(competition_id, data_dir, work_dir)
    submission_path = work_dir / "submission.csv"

    cmd = [
        sys.executable, agent_script,
        "--data_dir", str(comp_data),
        "--desc_file", str(desc_file),
        "--output", str(submission_path),
        "--timeout", str(timeout),
        *extra_args,
    ]

    logger.info(f"Running agent on: {competition_id}")
    start = time.time()

    try:
        proc = subprocess.run(cmd, timeout=timeout + 120, capture_output=True, text=True)
        elapsed = time.time() - start
        agent_success = proc.returncode == 0 and submission_path.exists()

        (work_dir / "stdout.log").write_text(proc.stdout[-20000:] if proc.stdout else "")
        (work_dir / "stderr.log").write_text(proc.stderr[-20000:] if proc.stderr else "")

    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        agent_success = submission_path.exists()

    # Grade with official mlebench grader
    grade_report = None
    if submission_path.exists():
        logger.info(f"  Grading submission...")
        grade_report = grade_submission(competition_id, submission_path, data_dir)
        if grade_report and "score" in grade_report:
            medal = "GOLD" if grade_report.get("gold_medal") else \
                    "SILVER" if grade_report.get("silver_medal") else \
                    "BRONZE" if grade_report.get("bronze_medal") else \
                    "above_median" if grade_report.get("above_median") else "below_median"
            logger.info(f"  Score: {grade_report['score']:.4f} | {medal}")
        elif grade_report and "error" in grade_report:
            logger.warning(f"  Grading error: {grade_report['error'][:200]}")

    result = {
        "competition_id": competition_id,
        "agent_success": agent_success,
        "elapsed_seconds": elapsed,
        "submission_exists": submission_path.exists(),
        "grade_report": grade_report,
    }

    # Save per-competition result
    (work_dir / "result.json").write_text(json.dumps(result, indent=2, default=str))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", type=str, required=True, help="Path to agent entry script")
    parser.add_argument("--competition", type=str, required=True, help="Competition ID(s), comma-separated")
    parser.add_argument("--data-dir", type=str, required=True, help="Path to prepared mlebench data")
    parser.add_argument("--output-dir", type=str, default="./results")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("agent_args", nargs="*")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    competitions = [c.strip() for c in args.competition.split(",")]
    results = []

    for comp in competitions:
        result = run_competition(
            agent_script=args.agent,
            competition_id=comp,
            data_dir=args.data_dir,
            output_dir=output_dir,
            timeout=args.timeout,
            extra_args=args.agent_args,
        )
        results.append(result)

    # Summary
    graded = [r for r in results if r.get("grade_report") and "score" in r.get("grade_report", {})]
    medals = sum(1 for r in graded if r["grade_report"].get("any_medal"))

    summary = {
        "total_competitions": len(results),
        "submissions_produced": sum(1 for r in results if r["submission_exists"]),
        "successfully_graded": len(graded),
        "medals": medals,
        "results": results,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))

    logger.info(f"\n{'='*50}")
    logger.info(f"Summary: {len(graded)}/{len(results)} graded, {medals} medals")
    for r in results:
        gr = r.get("grade_report", {})
        score = gr.get("score", "N/A")
        medal = "🥇" if gr.get("gold_medal") else "🥈" if gr.get("silver_medal") else \
                "🥉" if gr.get("bronze_medal") else "✓" if gr.get("above_median") else "✗"
        logger.info(f"  {r['competition_id']}: score={score} {medal} ({r['elapsed_seconds']:.0f}s)")
    logger.info(f"{'='*50}")


if __name__ == "__main__":
    main()
