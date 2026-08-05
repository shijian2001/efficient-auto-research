#!/usr/bin/env python3

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "source"
DATA = ROOT / "data"


def main() -> int:
    competitions = [
        line.strip()
        for line in (SOURCE / "experiments/splits/low.txt").read_text().splitlines()
        if line.strip()
    ]
    rows = []
    for competition in competitions:
        competition_dir = DATA / competition
        prepared = competition_dir / "prepared"
        prepared_files = list(prepared.rglob("*")) if prepared.is_dir() else []
        prepared_files = [path for path in prepared_files if path.is_file()]
        config_path = SOURCE / "mlebench/competitions" / competition / "config.yaml"
        config = yaml.safe_load(config_path.read_text())
        dataset_config = config.get("dataset") or {}
        required = [
            dataset_config[key]
            for key in ("answers", "gold_submission", "sample_submission")
            if dataset_config.get(key)
        ]
        missing_required = [relative for relative in required if not (DATA / relative).is_file()]
        rows.append(
            {
                "competition": competition,
                "directory": competition_dir.is_dir(),
                "archive": (competition_dir / f"{competition}.zip").is_file(),
                "raw_directory": (competition_dir / "raw").is_dir(),
                "prepared_directory": prepared.is_dir(),
                "prepared_file_count": len(prepared_files),
                "prepared_bytes": sum(path.stat().st_size for path in prepared_files),
                "missing_required_dataset_files": missing_required,
            }
        )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lite_task_count": len(rows),
        "directory_count": sum(row["directory"] for row in rows),
        "prepared_nonempty_count": sum(row["prepared_file_count"] > 0 for row in rows),
        "required_files_complete_count": sum(
            not row["missing_required_dataset_files"] for row in rows
        ),
        "missing_directories": [row["competition"] for row in rows if not row["directory"]],
        "missing_or_empty_prepared": [
            row["competition"] for row in rows if row["prepared_file_count"] == 0
        ],
        "missing_required_dataset_files": {
            row["competition"]: row["missing_required_dataset_files"]
            for row in rows
            if row["missing_required_dataset_files"]
        },
        "competitions": rows,
    }
    output = ROOT / "config/lite_data_audit.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
