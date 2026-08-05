#!/usr/bin/env python3

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def command_output(command: list[str]) -> str:
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return (result.stdout or result.stderr).strip()


def main() -> int:
    subprocess.run([str(ROOT / "scripts/audit_lite.py")], check=True, capture_output=True)
    audit = json.loads((ROOT / "config/lite_data_audit.json").read_text())
    status_path = ROOT / "config/siim_prepare_status.env"
    siim_status = {}
    if status_path.is_file():
        for line in status_path.read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                siim_status[key] = value

    current_blockers = sorted(
        set(audit["missing_directories"])
        | set(audit["missing_or_empty_prepared"])
        | set(audit["missing_required_dataset_files"])
    )
    data_complete = (
        audit["directory_count"] == audit["lite_task_count"]
        and audit["prepared_nonempty_count"] == audit["lite_task_count"]
        and audit["required_files_complete_count"] == audit["lite_task_count"]
        and not current_blockers
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": command_output(["git", "-C", str(ROOT / "source"), "rev-parse", "HEAD"]),
        "python_version": command_output([str(ROOT / ".venv/bin/python"), "--version"]),
        "mlebench_version": command_output(
            [str(ROOT / ".venv/bin/python"), "-c", "import importlib.metadata as m; print(m.version('mlebench'))"]
        ),
        "tensorflow_version": command_output(
            [str(ROOT / ".venv/bin/python"), "-c", "import importlib.metadata as m; print(m.version('tensorflow'))"]
        ),
        "lite_data": {
            "task_count": audit["lite_task_count"],
            "directory_count": audit["directory_count"],
            "prepared_nonempty_count": audit["prepared_nonempty_count"],
            "required_files_complete_count": audit["required_files_complete_count"],
            "missing_directories": audit["missing_directories"],
            "missing_or_empty_prepared": audit["missing_or_empty_prepared"],
            "missing_required_dataset_files": audit["missing_required_dataset_files"],
            "complete": data_complete,
        },
        "siim_prepare": siim_status,
        "current_blockers": current_blockers,
    }
    output = ROOT / "config/install_verification.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
