"""Host-owned official MLE-Bench grader worker.

This module is executed with the locked MLE-Bench Python, outside every Agent
namespace.  It intentionally imports no adapter or Agent code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
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
    payload.update(
        {
            "schema_version": 2,
            "competition_id": args.competition_id,
            "submission_sha256": hashlib.sha256(args.submission.read_bytes()).hexdigest(),
        }
    )
    payload["grader_report_digest"] = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(
            prefix=f".{output.name}.", suffix=".tmp", dir=output.parent, text=True
        )
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, output)
    except FileExistsError as exc:
        raise RuntimeError(f"refusing to overwrite grader report: {output}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return 0 if payload["valid_submission"] and payload["score"] is not None else 3


if __name__ == "__main__":
    raise SystemExit(main())
