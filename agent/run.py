"""
Agent entry point.

Usage:
    python agent/run.py --data_dir /path/to/data --desc_file /path/to/desc.md --output /path/to/submission.csv
"""

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("AutoResearch")


def main():
    parser = argparse.ArgumentParser(description="AutoResearch Agent")
    parser.add_argument("--data_dir", type=str, required=True)
    parser.add_argument("--desc_file", type=str, required=True)
    parser.add_argument("--output", type=str, required=True)
    parser.add_argument("--timeout", type=int, default=43200)
    parser.add_argument("--max_steps", type=int, default=50)
    parser.add_argument("--model", type=str, default=None)
    args = parser.parse_args()

    model = args.model or "gpt-4o"
    data_dir = Path(args.data_dir).resolve()
    desc_file = Path(args.desc_file).resolve()
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    task_desc = desc_file.read_text() if desc_file.exists() else ""
    work_dir = output_path.parent / "workspace"
    work_dir.mkdir(parents=True, exist_ok=True)

    from agent.engine.search import GraphSearchEngine, SearchConfig

    config = SearchConfig(
        max_steps=args.max_steps,
        time_limit=args.timeout,
        model=model,
        exec_timeout=min(3600, args.timeout // 3),
    )

    logger.info(f"Starting: model={model}, steps={args.max_steps}")
    engine = GraphSearchEngine(task_desc=task_desc, data_dir=data_dir, work_dir=work_dir, config=config)
    result = engine.run()

    if result and result.exists():
        shutil.copy2(result, output_path)
        logger.info(f"Submission saved to {output_path}")
        return 0
    else:
        logger.error("No submission produced")
        return 1


if __name__ == "__main__":
    sys.exit(main())
