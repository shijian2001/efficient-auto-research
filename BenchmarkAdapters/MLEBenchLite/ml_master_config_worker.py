"""Generate an isolated ML-Master 2 config in its locked environment."""

from __future__ import annotations

import argparse
import copy
import shutil
from pathlib import Path

import yaml


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--competition-id", required=True)
    parser.add_argument("--public-dir", type=Path, required=True)
    parser.add_argument("--staged-data-root", type=Path, required=True)
    parser.add_argument("--workspace-dir", type=Path, required=True)
    parser.add_argument("--gpu-id", type=int, required=True)
    parser.add_argument("--timeout", type=int, required=True)
    args = parser.parse_args()

    if args.destination.exists() or args.staged_data_root.exists() or args.workspace_dir.exists():
        raise RuntimeError("refusing to overwrite an ML-Master 2 per-run config or workspace")
    payload = yaml.safe_load(args.template.read_text(encoding="utf-8"))
    public_destination = (
        args.staged_data_root / args.competition_id / "prepared/public"
    )
    public_destination.parent.mkdir(parents=True, exist_ok=False)
    shutil.copytree(args.public_dir, public_destination)
    payload["competition_id"] = args.competition_id
    payload["data_root"] = str(args.staged_data_root.resolve())
    payload["grading_servers"] = []
    payload["llm"] = {"openai": copy.deepcopy(payload["llm"]["openai"]), "default": "openai"}
    payload["llm"]["openai"]["reasoning_effort"] = "high"
    for agent in payload.get("agents", {}).values():
        if isinstance(agent, dict) and "llm" in agent:
            agent["llm"] = "openai"
    local = payload["session"]["local"]
    local["working_dir"] = str(args.workspace_dir.resolve())
    local["workspace_path"] = str(args.workspace_dir.resolve())
    local["timeout"] = args.timeout
    local["gpu_devices"] = [str(args.gpu_id)]
    local["cpu_devices"] = None
    local["symlinks"] = {str(public_destination.resolve()): "input"}
    local["parallel"] = {
        "enabled": False,
        "max_parallel": 1,
        "split_workspace_for_exp": False,
    }
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    with args.destination.open("x", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
