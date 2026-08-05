#!/usr/bin/env python3

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def command_output(command: list[str]) -> str:
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return (result.stdout or result.stderr).strip()


def latest_successful_oracle() -> dict[str, object] | None:
    successful = []
    for result_path in ROOT.glob("jobs/*/*/result.json"):
        try:
            result = json.loads(result_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        verifier_result = result.get("verifier_result") or {}
        rewards = verifier_result.get("rewards") or {}
        agent_info = result.get("agent_info") or {}
        reward = rewards.get("reward")
        if agent_info.get("name") == "oracle" and reward == 1.0:
            successful.append((result.get("finished_at", ""), result_path, result))

    if not successful:
        return None

    _, result_path, result = max(successful, key=lambda item: item[0])
    return {
        "result_path": str(result_path.relative_to(ROOT)),
        "task_name": result.get("task_name"),
        "reward": result["verifier_result"]["rewards"]["reward"],
        "finished_at": result.get("finished_at"),
    }


def main() -> int:
    manifest = json.loads((ROOT / "config/dataset_manifest.json").read_text())
    checks = {
        "harbor_version": command_output([str(ROOT / ".venv/bin/harbor"), "--version"]),
        "python_version": command_output([str(ROOT / ".venv/bin/python"), "--version"]),
        "docker_version": command_output(["docker", "version", "--format", "{{.Server.Version}}"]),
        "dataset_task_count": manifest["task_count"],
        "dataset_missing_required_files": len(manifest["missing_required_files"]),
        "dataset_maximum_gpus": manifest["resource_summary"]["gpus"]["maximum"],
        "dataset_sha256": manifest["sha256"],
        "latest_successful_oracle": latest_successful_oracle(),
    }
    passed = {
        "harbor_0_20_0": "0.20.0" in checks["harbor_version"],
        "python_3_12": checks["python_version"].startswith("Python 3.12."),
        "docker_available": bool(checks["docker_version"]),
        "dataset_has_89_tasks": checks["dataset_task_count"] == 89,
        "dataset_files_complete": checks["dataset_missing_required_files"] == 0,
        "dataset_requires_no_gpu": checks["dataset_maximum_gpus"] == 0,
        "oracle_smoke_passed": checks["latest_successful_oracle"] is not None,
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "installation_root": str(ROOT),
        "status": "passed" if all(passed.values()) else "failed",
        "checks": checks,
        "passed": passed,
    }
    output_path = ROOT / "config/install_verification.json"
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
