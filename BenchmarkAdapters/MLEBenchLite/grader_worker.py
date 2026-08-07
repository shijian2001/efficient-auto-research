"""Host-owned official MLE-Bench grader worker.

This module is executed with the locked MLE-Bench Python, outside every Agent
namespace.  It intentionally imports no adapter or Agent code.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mlebench.grade import grade_csv
from mlebench.registry import Registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--competition-id", required=True)
    parser.add_argument("--submission", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    report = grade_csv(
        args.submission.resolve(),
        Registry(data_dir=args.data_root.resolve()).get_competition(args.competition_id),
    )
    payload = report.to_dict()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    return 0 if payload["valid_submission"] and payload["score"] is not None else 3


if __name__ == "__main__":
    raise SystemExit(main())
