#!/usr/bin/env python3
"""
对 Docker 容器产出的 submission 做 mlebench 官方评分。

用法:
    python grade.py <competition> <submission.csv>
    python grade.py spooky-author-identification /path/to/submission.csv

如果不传 submission 路径，会自动按 agent 在默认输出位置查找。
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from mlebench.registry import Registry
from mlebench.grade import grade_csv

DATA_DIR = Path("/mnt/sdc/shijianwang/efficient-agent-research/mle-bench-data")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bind_arbor_grade(run_dir: Path, comp_id: str, sub_path: Path, report) -> Path:
    manifest_path = run_dir / "submission_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Arbor manifest is missing or invalid: {manifest_path}") from exc
    expected_path = Path(str(manifest.get("submission", ""))).resolve()
    actual_path = sub_path.resolve()
    expected_hash = str(manifest.get("sha256", ""))
    actual_hash = _sha256(sub_path)
    if (
        manifest.get("competition_id") != comp_id
        or manifest.get("fallback") is True
        or expected_path != actual_path
        or not expected_hash
        or expected_hash != actual_hash
    ):
        raise SystemExit("Arbor final submission path/hash does not match submission_manifest.json")
    record = {
        "schema_version": 1,
        "run_id": manifest.get("run_id"),
        "agent": manifest.get("agent"),
        "agent_variant": manifest.get("agent_variant"),
        "competition_id": comp_id,
        "submission": str(actual_path),
        "submission_sha256": actual_hash,
        "evaluation_role": "official_private_mlebench",
        "grader": "mlebench.grade.grade_csv",
        "data_root": str(DATA_DIR),
        "score": report.score,
        "valid_submission": report.valid_submission,
        "gold_medal": report.gold_medal,
        "silver_medal": report.silver_medal,
        "bronze_medal": report.bronze_medal,
        "above_median": report.above_median,
        "any_medal": report.any_medal,
    }
    output = run_dir / "official_grading.json"
    try:
        with output.open("x", encoding="utf-8") as handle:
            json.dump(record, handle, sort_keys=True, indent=2)
            handle.write("\n")
    except FileExistsError as exc:
        raise SystemExit(f"refusing to overwrite Arbor official grading record: {output}") from exc
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("competition")
    parser.add_argument("submission", type=Path)
    parser.add_argument(
        "--arbor-run-dir",
        type=Path,
        help="Bind the official result to an Arbor submission_manifest.json",
    )
    args = parser.parse_args()
    comp_id, sub_path = args.competition, args.submission

    comp = Registry(data_dir=DATA_DIR).get_competition(comp_id)
    if not sub_path.exists():
        print(f"submission 不存在: {sub_path}")
        sys.exit(1)

    rep = grade_csv(sub_path, comp)
    print(f"competition : {comp_id}")
    print(f"submission  : {sub_path}")
    print(f"score       : {rep.score}")
    print(f"valid       : {rep.valid_submission}")
    print(f"gold   (<={rep.gold_threshold})   : {rep.gold_medal}")
    print(f"silver (<={rep.silver_threshold}) : {rep.silver_medal}")
    print(f"bronze (<={rep.bronze_threshold}) : {rep.bronze_medal}")
    print(f"median (<={rep.median_threshold}) : {rep.above_median}")
    print(f"any_medal   : {rep.any_medal}")
    if args.arbor_run_dir is not None:
        record = _bind_arbor_grade(args.arbor_run_dir.resolve(), comp_id, sub_path.resolve(), rep)
        print(f"official_record: {record}")


if __name__ == "__main__":
    main()
